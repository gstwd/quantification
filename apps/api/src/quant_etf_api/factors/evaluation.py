"""因子评估模块：IC/IR 分析与因子相关性矩阵。

提供因子效力评估的核心计算逻辑：
- Rank IC：因子值与下期收益的 Spearman 秩相关系数
- IC 时间序列与汇总统计（IC 均值、IC 标准差、IC_IR、t 统计量、IC>0 占比）
- 因子间截面 Rank 相关矩阵

**统计口径约定**

1. 横截面是"当日有该因子值的指数集合"。本系统只有 30 个指数（2020–2025 年
   长期只有 8 个），横截面过小时 Spearman 秩相关的取值粒度极粗（N=3 时只能取
   ±1/±0.5），因此有效 IC 要求横截面指数数量不少于 ``MIN_CROSS_SECTION_N``；
   不足的交易日既不进入序列也不进入汇总，但会计入 ``excluded_low_n_days``
   并在 ``insufficient_reason`` 中说明，绝不静默。
2. 前瞻日期按**观测交易日并集**对齐：T 的前瞻日是日历上第 ``forward_days`` 个
   交易日。某指数若在前瞻日当天没有行情，则该指数从当日的横截面中剔除，
   而不是顺延到更晚的 bar —— 否则同一横截面内各指数的前瞻窗口长度不一致。
3. ``ic_ir`` 为未年化口径（``ic_mean / ic_std``），与用户手册阈值一致；
   显著性看 ``t_stat = ic_ir × sqrt(effective_n)``。
4. ``forward_days > 1`` 时相邻观测的前瞻窗口互相重叠，``ic_std`` 会低估真实
   波动，故汇总同时返回 ``effective_n = count // forward_days`` 与 ``overlap``，
   供使用者自行折价。
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from scipy.stats import spearmanr
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_factor_value import IndexFactorValueRepository

# 有效 Rank IC 所需的最小横截面指数数量
MIN_CROSS_SECTION_N: int = 20
# 前瞻取价窗口上限（自然日）。API 允许 forward_days ≤ 20（约 28 个自然日），
# 60 天足以覆盖长假，且让区间末端缺少前瞻行情的日期自然被丢弃。
FORWARD_LOOKAHEAD_CALENDAR_DAYS: int = 60

# IC 观测状态：有效 / 横截面不足 / 缺少前瞻行情
STATUS_OK = "ok"
STATUS_LOW_N = "low_n"
STATUS_NO_FORWARD = "no_forward"


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """计算 Spearman 秩相关系数并保留 4 位小数。

    Args:
        xs: 第一组数值。
        ys: 第二组数值，长度需与 ``xs`` 一致。

    Returns:
        秩相关系数（-1 到 1，保留 4 位小数）；样本不足或结果非有限值
        （常数输入导致未定义）时返回 None。
    """
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    # 常数输入（横截面因子值恒定）时 spearman 会给出未定义的相关系数并告警，
    # 这属于预期情况，不污染日志
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        corr, _ = spearmanr(xs, ys)
    value = float(corr)
    if not math.isfinite(value):
        return None
    return round(value, 4)


def calc_rank_ic_from_values(
    factor_values: Mapping[str, float],
    forward_returns: Mapping[str, float],
    min_n: int = MIN_CROSS_SECTION_N,
) -> float | None:
    """由同日因子值与前瞻收益计算 Rank IC（纯函数）。

    Args:
        factor_values: 指数代码 → 因子值。
        forward_returns: 指数代码 → 前瞻收益率（量纲不影响秩相关）。
        min_n: 参与计算所需的最小配对样本数。

    Returns:
        Rank IC（保留 4 位小数）；配对样本少于 ``min_n`` 或相关未定义时返回 None。
    """
    paired_factor: list[float] = []
    paired_return: list[float] = []
    for code, value in factor_values.items():
        ret = forward_returns.get(code)
        if ret is None:
            continue
        paired_factor.append(value)
        paired_return.append(ret)
    if len(paired_factor) < min_n:
        return None
    return _spearman(paired_factor, paired_return)


def build_forward_returns(
    price_lookup: Mapping[tuple[str, date], float],
    trading_dates: Sequence[date],
    forward_days: int,
) -> dict[date, dict[str, float]]:
    """按统一交易日历构建每个交易日的指数前瞻收益率（纯函数）。

    前瞻日定义为 ``trading_dates`` 中 T 之后的第 ``forward_days`` 个交易日。
    只有在前瞻日**当天**有有效收盘价的指数才进入结果，缺行情的指数被剔除，
    不会顺延到更晚的 bar。

    Args:
        price_lookup: (指数代码, 交易日) → 收盘价。
        trading_dates: 升序交易日列表（观测到的日历）。
        forward_days: 前瞻交易日数量，需 ≥ 1。

    Returns:
        交易日 → {指数代码: 前瞻收益率}；日历长度不足以覆盖前瞻窗口的日期不出现。
    """
    if forward_days < 1:
        return {}
    # 日历上仍有前瞻窗口的日期先占位（空字典），保证"窗口不存在"与
    # "窗口存在但没有指数合格"两种情况可区分
    result: dict[date, dict[str, float]] = {
        trade_date: {}
        for index, trade_date in enumerate(trading_dates)
        if index + forward_days < len(trading_dates)
    }
    if not result:
        return result

    position = {trade_date: index for index, trade_date in enumerate(trading_dates)}
    by_index: dict[str, dict[date, float]] = {}
    for (code, bar_date), close in price_lookup.items():
        if close > 0:
            by_index.setdefault(code, {})[bar_date] = close

    for code, series in by_index.items():
        for bar_date, close in series.items():
            index = position.get(bar_date)
            if index is None or index + forward_days >= len(trading_dates):
                continue
            forward_date = trading_dates[index + forward_days]
            forward_close = series.get(forward_date)
            if forward_close is None:
                continue
            result[bar_date][code] = (forward_close / close - 1) * 100
    return result


def build_ic_observation(
    trade_date: date,
    factor_values: Mapping[str, float],
    forward_returns: Mapping[str, float],
    min_n: int,
) -> dict[str, Any]:
    """构造单个交易日的 IC 观测（纯函数）。

    Args:
        trade_date: 因子值对应的交易日。
        factor_values: 指数代码 → 因子值。
        forward_returns: 指数代码 → 前瞻收益率。
        min_n: 有效 IC 所需的最小配对样本数。

    Returns:
        含 ``trade_date`` / ``ic`` / ``cross_section_n`` / ``status`` 的字典；
        ``status`` 为 ``ok`` / ``low_n``；``cross_section_n`` 为配对后的实际样本数。
    """
    paired = sum(1 for code in factor_values if code in forward_returns)
    if paired < min_n:
        return {
            "trade_date": trade_date,
            "ic": None,
            "cross_section_n": paired,
            "status": STATUS_LOW_N,
        }
    ic = calc_rank_ic_from_values(factor_values, forward_returns, min_n)
    if ic is None:
        # 配对样本达标但相关未定义（常数横截面）
        return {
            "trade_date": trade_date,
            "ic": None,
            "cross_section_n": paired,
            "status": STATUS_LOW_N,
        }
    return {
        "trade_date": trade_date,
        "ic": ic,
        "cross_section_n": paired,
        "status": STATUS_OK,
    }


def summarize_ic(
    observations: Sequence[Mapping[str, Any]],
    forward_days: int,
    min_n: int = MIN_CROSS_SECTION_N,
) -> dict[str, Any]:
    """汇总 IC 观测序列（纯函数，IC 汇总统计的唯一实现）。

    只有 ``status == "ok"`` 的观测进入统计量；``low_n``（横截面不足）与
    ``no_forward``（缺少前瞻行情）分别计数并给出原因。

    Args:
        observations: ``build_ic_observation`` 产出的观测列表。
        forward_days: 前瞻交易日数量，用于折算 ``effective_n``。
        min_n: 有效 IC 所需的最小横截面指数数量（回显给调用方）。

    Returns:
        含 ic_mean / ic_std / ic_ir / t_stat / ic_positive_ratio / count /
        effective_n / overlap / cross_section_n_* / excluded_low_n_days /
        dropped_no_forward_days / insufficient_reason 的字典。
    """
    ok = [item for item in observations if item.get("status") == STATUS_OK]
    low_n_days = sum(1 for item in observations if item.get("status") == STATUS_LOW_N)
    no_forward_days = sum(
        1 for item in observations if item.get("status") == STATUS_NO_FORWARD
    )
    max_n_seen = max(
        (
            int(item.get("cross_section_n") or 0)
            for item in observations
            if item.get("status") != STATUS_NO_FORWARD
        ),
        default=None,
    )
    cross_section_sizes = [int(item.get("cross_section_n") or 0) for item in ok]

    summary: dict[str, Any] = {
        "ic_mean": None,
        "ic_std": None,
        "ic_ir": None,
        "t_stat": None,
        "ic_positive_ratio": None,
        "count": 0,
        "effective_n": 0,
        "overlap": forward_days > 1,
        "cross_section_n_avg": None,
        "cross_section_n_min": None,
        "cross_section_n_max": None,
        "cross_section_n_required": min_n,
        "excluded_low_n_days": low_n_days,
        "dropped_no_forward_days": no_forward_days,
        "insufficient_reason": None,
    }
    if not ok:
        summary["insufficient_reason"] = _insufficient_reason(
            observations, low_n_days, no_forward_days, max_n_seen, min_n
        )
        return summary

    ic_values = [float(item["ic"]) for item in ok]
    n = len(ic_values)
    mean = sum(ic_values) / n
    variance = sum((x - mean) ** 2 for x in ic_values) / (n - 1) if n > 1 else 0.0
    std = variance**0.5
    ic_ir = round(mean / std, 4) if std > 0 else None
    effective_n = n // forward_days if forward_days > 1 else n

    summary.update(
        {
            "ic_mean": round(mean, 4),
            "ic_std": round(std, 4),
            "ic_ir": ic_ir,
            "t_stat": round(ic_ir * math.sqrt(effective_n), 4) if ic_ir is not None else None,
            "ic_positive_ratio": round(sum(1 for x in ic_values if x > 0) / n, 4),
            "count": n,
            "effective_n": effective_n,
            "cross_section_n_avg": round(sum(cross_section_sizes) / n, 1),
            "cross_section_n_min": min(cross_section_sizes),
            "cross_section_n_max": max(cross_section_sizes),
        }
    )
    return summary


def _insufficient_reason(
    observations: Sequence[Mapping[str, Any]],
    low_n_days: int,
    no_forward_days: int,
    max_n_seen: int | None,
    min_n: int,
) -> str:
    """生成"未产出有效 IC"的可读原因。"""
    if not observations:
        return "区间内没有该因子的因子值，无法计算 Rank IC"
    parts: list[str] = []
    if low_n_days:
        parts.append(
            f"{low_n_days} 个交易日横截面不足 {min_n} 个指数"
            + (f"（最多 {max_n_seen} 个）" if max_n_seen else "")
        )
    if no_forward_days:
        parts.append(f"{no_forward_days} 个交易日缺少前瞻行情")
    if not parts:
        return "区间内没有可用于计算 Rank IC 的观测"
    return "；".join(parts) + "，未产出有效 Rank IC"


def _load_ic_observations(
    db: Session,
    factor_id: str,
    start_date: date,
    end_date: date,
    forward_days: int,
    min_n: int,
) -> list[dict[str, Any]]:
    """批量加载并构造区间内的 IC 观测（三次区间查询，无逐日往返）。

    Args:
        db: SQLAlchemy 同步 Session。
        factor_id: 因子标识。
        start_date: 起始日期（含）。
        end_date: 截止日期（含）。
        forward_days: 前瞻交易日数量。
        min_n: 有效 IC 所需的最小横截面指数数量。

    Returns:
        按交易日升序的观测列表（含 low_n / no_forward 观测）。
    """
    factor_rows = IndexFactorValueRepository(db).find_factor_series_range(
        factor_id, start_date, end_date
    )
    if not factor_rows:
        return []

    values_by_date: dict[date, dict[str, float]] = {}
    for trade_date, index_code, value in factor_rows:
        values_by_date.setdefault(trade_date, {})[index_code] = value
    index_codes = sorted({code for _, code, _ in factor_rows})

    window_end = end_date + timedelta(days=FORWARD_LOOKAHEAD_CALENDAR_DAYS)
    bar_repo = IndexDailyBarRepository(db)
    # 日历取全部指数的观测并集：不因某个因子只覆盖少数指数而丢失交易日
    trading_dates = bar_repo.find_all_trading_dates(start_date, window_end)
    if not trading_dates:
        return [
            {
                "trade_date": trade_date,
                "ic": None,
                "cross_section_n": 0,
                "status": STATUS_NO_FORWARD,
            }
            for trade_date in sorted(values_by_date)
        ]

    bar_map = bar_repo.find_all_date_range(start_date, window_end, index_codes)
    price_lookup: dict[tuple[str, date], float] = {}
    for (code, bar_date), row in bar_map.items():
        if row.close_price is not None:
            price_lookup[(code, bar_date)] = float(row.close_price)

    forward_returns = build_forward_returns(price_lookup, trading_dates, forward_days)

    observations: list[dict[str, Any]] = []
    for trade_date in sorted(values_by_date):
        per_index = forward_returns.get(trade_date)
        if per_index is None:
            observations.append(
                {
                    "trade_date": trade_date,
                    "ic": None,
                    "cross_section_n": 0,
                    "status": STATUS_NO_FORWARD,
                }
            )
            continue
        observations.append(
            build_ic_observation(
                trade_date, values_by_date[trade_date], per_index, min_n
            )
        )
    return observations


def analyze_ic(
    db: Session,
    factor_id: str,
    start_date: date,
    end_date: date,
    forward_days: int = 1,
    min_n: int = MIN_CROSS_SECTION_N,
) -> dict[str, Any]:
    """一次加载同时产出 IC 序列与汇总统计（推荐入口）。

    Args:
        db: SQLAlchemy 同步 Session。
        factor_id: 因子标识。
        start_date: 起始日期（含）。
        end_date: 截止日期（含）。
        forward_days: 前瞻交易日数量，需 ≥ 1。
        min_n: 有效 IC 所需的最小横截面指数数量。

    Returns:
        ``{"series": [...], "summary": {...}}``；``series`` 每项含
        trade_date / ic / cross_section_n，按日期升序且只含有效观测。
    """
    if start_date > end_date or forward_days < 1:
        return {"series": [], "summary": summarize_ic([], forward_days, min_n)}
    observations = _load_ic_observations(
        db, factor_id, start_date, end_date, forward_days, min_n
    )
    summary = summarize_ic(observations, forward_days, min_n)
    series = [
        {
            "trade_date": str(item["trade_date"]),
            "ic": float(item["ic"]),
            "cross_section_n": int(item["cross_section_n"]),
        }
        for item in observations
        if item.get("status") == STATUS_OK
    ]
    return {"series": series, "summary": summary}


def calc_ic_series(
    db: Session,
    factor_id: str,
    start_date: date,
    end_date: date,
    forward_days: int = 1,
    min_n: int = MIN_CROSS_SECTION_N,
) -> list[dict[str, Any]]:
    """计算因子在指定时间范围内的 IC 时间序列。

    Args:
        db: SQLAlchemy 同步 Session。
        factor_id: 因子标识。
        start_date: 起始日期（含）。
        end_date: 截止日期（含）。
        forward_days: 前瞻交易日数量。
        min_n: 有效 IC 所需的最小横截面指数数量。

    Returns:
        按日期升序排列的 IC 序列，每项包含 trade_date / ic / cross_section_n。
    """
    return analyze_ic(db, factor_id, start_date, end_date, forward_days, min_n)["series"]


def calc_ic_summary(
    db: Session,
    factor_id: str,
    start_date: date,
    end_date: date,
    forward_days: int = 1,
    min_n: int = MIN_CROSS_SECTION_N,
) -> dict[str, Any]:
    """汇总因子 IC 统计信息。

    Args:
        db: SQLAlchemy 同步 Session。
        factor_id: 因子标识。
        start_date: 起始日期（含）。
        end_date: 截止日期（含）。
        forward_days: 前瞻交易日数量。
        min_n: 有效 IC 所需的最小横截面指数数量。

    Returns:
        见 ``summarize_ic`` 的返回说明。
    """
    return analyze_ic(db, factor_id, start_date, end_date, forward_days, min_n)["summary"]


def calc_rank_ic(
    db: Session,
    factor_id: str,
    trade_date: date,
    forward_days: int = 1,
    min_n: int = MIN_CROSS_SECTION_N,
) -> float | None:
    """计算单日 Rank IC（因子值与下期收益的 Spearman 秩相关系数）。

    Args:
        db: SQLAlchemy 同步 Session。
        factor_id: 因子标识。
        trade_date: 因子值对应的交易日。
        forward_days: 前瞻交易日数量。
        min_n: 有效 IC 所需的最小横截面指数数量。

    Returns:
        Rank IC 值（-1 到 1）；横截面不足或缺少前瞻行情时返回 None。
    """
    observations = _load_ic_observations(
        db, factor_id, trade_date, trade_date, forward_days, min_n
    )
    for item in observations:
        if item.get("status") == STATUS_OK:
            return float(item["ic"])
    return None


def pairwise_rank_correlation(
    values_by_factor: Mapping[str, Mapping[str, float]],
    min_n: int = MIN_CROSS_SECTION_N,
) -> tuple[list[str], list[list[float | None]], list[list[int]], int]:
    """计算因子两两之间的截面 Rank 相关系数（纯函数）。

    每一对因子使用**该对自身**的交集指数（而不是"所有因子都有值"的全局交集），
    否则因子覆盖不一致时交集会塌缩到少数指数甚至为空。

    Args:
        values_by_factor: 因子标识 → {指数代码: 因子值}。
        min_n: 计算相关系数所需的最小成对样本数。

    Returns:
        (factor_ids, matrix, pair_counts, undetermined_pair_count)；
        ``matrix[i][j]`` 在样本不足或相关未定义时为 None（不伪造为 0），
        对角线恒为 1.0；``pair_counts[i][j]`` 为该对的实际交集指数数量。
    """
    factor_ids = sorted(values_by_factor)
    size = len(factor_ids)
    matrix: list[list[float | None]] = [[None] * size for _ in range(size)]
    pair_counts: list[list[int]] = [[0] * size for _ in range(size)]
    undetermined = 0
    for i in range(size):
        left = values_by_factor[factor_ids[i]]
        matrix[i][i] = 1.0
        pair_counts[i][i] = len(left)
        for j in range(i + 1, size):
            right = values_by_factor[factor_ids[j]]
            common = [code for code in left if code in right]
            pair_counts[i][j] = pair_counts[j][i] = len(common)
            corr = None
            if len(common) >= min_n:
                corr = _spearman(
                    [left[code] for code in common], [right[code] for code in common]
                )
            matrix[i][j] = matrix[j][i] = corr
            if corr is None:
                undetermined += 1
    return factor_ids, matrix, pair_counts, undetermined


def calc_factor_correlation_matrix(
    db: Session,
    trade_date: date,
    factor_ids: list[str] | None = None,
    min_n: int = MIN_CROSS_SECTION_N,
) -> dict[str, Any]:
    """计算因子间截面 Rank 相关矩阵。

    对指定日期的所有指数，按**成对交集**计算各因子值之间的 Spearman 相关系数。

    Args:
        db: SQLAlchemy 同步 Session。
        trade_date: 交易日。
        factor_ids: 要计算的因子列表，None 表示所有有数据的因子。
        min_n: 计算相关系数所需的最小成对样本数。

    Returns:
        含 factor_ids / matrix / pair_counts / index_count /
        undetermined_pair_count / trade_date 的字典；``index_count`` 为当日
        有任一因子值的指数数量（原始横截面规模）。
    """
    rows = IndexFactorValueRepository(db).find_cross_section_values(
        trade_date, factor_ids
    )

    values_by_factor: dict[str, dict[str, float]] = {}
    index_codes: set[str] = set()
    for index_code, factor_id, value in rows:
        index_codes.add(index_code)
        values_by_factor.setdefault(factor_id, {})[index_code] = value

    if len(values_by_factor) < 2:
        return {
            "factor_ids": sorted(values_by_factor),
            "matrix": [],
            "pair_counts": [],
            "index_count": len(index_codes),
            "undetermined_pair_count": 0,
            "trade_date": str(trade_date),
        }

    factor_id_list, matrix, pair_counts, undetermined = pairwise_rank_correlation(
        values_by_factor, min_n
    )
    return {
        "factor_ids": factor_id_list,
        "matrix": matrix,
        "pair_counts": pair_counts,
        "index_count": len(index_codes),
        "undetermined_pair_count": undetermined,
        "trade_date": str(trade_date),
    }
