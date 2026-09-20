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

from quant_etf_api.factors.catalog import FactorInstance, FactorTemplateRegistry, get_factor_template_registry
from quant_etf_api.factors.compute import FactorComputeService, FactorMatrix
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository

# 有效 Rank IC 所需的最小横截面指数数量
MIN_CROSS_SECTION_N: int = 20
# 组合分数 IC 的横截面是"当日真正参与评分的资产集合"（候选池 ∩ 通过过滤规则），
# 其规模由策略的标的范围与过滤设计决定，通常远小于指数池。沿用单因子的 20 会让
# 窄池策略（如 7 个标的的范围）永久拿不到任何 IC 证据，故设独立下限；小横截面的
# 秩相关粒度粗，显著性交给 t_stat（有效样本数已按前瞻期折算）判断。
MIN_SCORE_CROSS_SECTION_N: int = 5
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
    already_non_overlapping: bool = False,
) -> dict[str, Any]:
    """汇总 IC 观测序列（纯函数，IC 汇总统计的唯一实现）。

    只有 ``status == "ok"`` 的观测进入统计量；``low_n``（横截面不足）与
    ``no_forward``（缺少前瞻行情）分别计数并给出原因。

    Args:
        observations: ``build_ic_observation`` 产出的观测列表。
        forward_days: 前瞻交易日数量，用于折算 ``effective_n``。
        min_n: 有效 IC 所需的最小横截面指数数量（回显给调用方）。
        already_non_overlapping: 观测本身是否已按前瞻跨度间隔开。
            该参数必须由实际交易日历验证；不能仅因观测来自调仓日便置 True。
            未重叠窗口再按 ``forward_days`` 折算会二次折扣，存在任一重叠
            时则应保持 False 以避免高估统计显著性。

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
        "overlap": forward_days > 1 and not already_non_overlapping,
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
    # 观测已按调仓周期间隔时不再折算；否则重叠窗口会让 ic_std 低估真实波动
    effective_n = n if already_non_overlapping or forward_days <= 1 else n // forward_days

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
    instance: FactorInstance,
    start_date: date,
    end_date: date,
    forward_days: int,
    min_n: int,
    registry: FactorTemplateRegistry | None = None,
) -> list[dict[str, Any]]:
    """批量加载并构造区间内的 IC 观测。

    横截面取该区间起点的 point-in-time 指数集合，因子值由现算服务一次算出，
    不做逐日往返查询；前瞻收益仍统一从指数日线构造。

    Args:
        db: SQLAlchemy 同步 Session。
        instance: 已解析的因子实例。
        start_date: 起始日期（含）。
        end_date: 截止日期（含）。
        forward_days: 前瞻交易日数量。
        min_n: 有效 IC 所需的最小横截面指数数量。
        registry: 因子模板注册表，None 时使用进程级单例。

    Returns:
        按交易日升序的观测列表（含 low_n / no_forward 观测）。
    """
    bar_repo = IndexDailyBarRepository(db)
    window_end = end_date + timedelta(days=FORWARD_LOOKAHEAD_CALENDAR_DAYS)
    # 日历取全部指数的观测并集：不因某个指数缺行情而丢失交易日
    trading_dates = bar_repo.find_all_trading_dates(start_date, window_end)
    if not trading_dates:
        return []
    observation_dates = [trade_date for trade_date in trading_dates if trade_date <= end_date]

    index_codes = [
        row.index_code
        for row in BenchmarkIndexRepository(db).find_for_period(start_date)
    ]
    bar_map = bar_repo.find_all_date_range(start_date, window_end, index_codes)
    price_lookup: dict[tuple[str, date], float] = {}
    for (code, bar_date), row in bar_map.items():
        if row.close_price is not None:
            price_lookup[(code, bar_date)] = float(row.close_price)

    forward_returns = build_forward_returns(price_lookup, trading_dates, forward_days)

    matrix = FactorMatrix()
    if observation_dates and index_codes:
        compute = FactorComputeService(db, registry or get_factor_template_registry())
        matrix = compute.compute_matrix([instance], index_codes, observation_dates)

    observations: list[dict[str, Any]] = []
    for trade_date in observation_dates:
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
        day = matrix.day(trade_date)
        values = {
            code: value
            for (code, ref), value in day.items()
            if ref == instance.instance_id and value is not None
        }
        observations.append(
            build_ic_observation(trade_date, values, per_index, min_n)
        )
    return observations


def analyze_ic(
    db: Session,
    instance: FactorInstance,
    start_date: date,
    end_date: date,
    forward_days: int = 1,
    min_n: int = MIN_CROSS_SECTION_N,
    registry: FactorTemplateRegistry | None = None,
) -> dict[str, Any]:
    """一次加载同时产出 IC 序列与汇总统计（推荐入口）。

    Args:
        db: SQLAlchemy 同步 Session。
        instance: 已解析的因子实例。
        start_date: 起始日期（含）。
        end_date: 截止日期（含）。
        forward_days: 前瞻交易日数量，需 ≥ 1。
        min_n: 有效 IC 所需的最小横截面指数数量。
        registry: 因子模板注册表，None 时使用进程级单例。

    Returns:
        ``{"series": [...], "summary": {...}}``；``series`` 每项含
        trade_date / ic / cross_section_n，按日期升序且只含有效观测。
    """
    if start_date > end_date or forward_days < 1:
        return {"series": [], "summary": summarize_ic([], forward_days, min_n)}
    observations = _load_ic_observations(
        db, instance, start_date, end_date, forward_days, min_n, registry
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


def analyze_backtest_score_ic(
    db: Session,
    backtest_id: str,
    start_date: date,
    end_date: date,
    forward_days: int = 1,
    min_n: int = MIN_SCORE_CROSS_SECTION_N,
) -> dict[str, Any]:
    """计算已落库策略综合分数的 Rank IC。

    综合分数仅取**实际执行选股调仓日**中真正参与评分的资产（``scored=True``），
    前瞻收益仍统一从指数日线构造，避免
    ``backtest_index_result.index_return`` 只记录入选资产而造成的横截面选择偏差。
    统计口径与单因子 :func:`analyze_ic` 完全一致。

    ``scored=False`` 的行必须排除：那些行的 ``signal_score`` 是占位 0.0（未被
    评分，或被候选池/过滤规则拒绝），把它们留在横截面里会占据固定底部名次，
    既扭曲 IC 均值，也压缩日间波动、虚高 t 值。

    横截面门槛默认取 ``MIN_SCORE_CROSS_SECTION_N``（5）而不是单因子口径的 20：
    这里的横截面是"当日评分集合"，其规模由策略的标的范围与过滤设计决定，
    沿用 20 会让窄池策略永久拿不到任何 IC 证据。

    Args:
        db: SQLAlchemy 同步 Session。
        backtest_id: 回测标识（须由迁移 0053 之后写入，否则缺少 scored 标记）。
        start_date: 监控区间起始日（含）。
        end_date: 监控区间截止日（含）。
        forward_days: 前瞻交易日数量，应与策略选股调仓周期对齐。
        min_n: 有效 IC 所需的最小**评分资产**数量。

    Returns:
        ``{"series": [...], "summary": {...}}``；``summary`` 在
        :func:`summarize_ic` 基础上追加 ``scored_n_avg`` / ``universe_n_avg`` /
        ``coverage_ratio`` / ``unscored_rows`` 四项覆盖度诊断。
    """
    if start_date > end_date or forward_days < 1:
        return {
            "series": [],
            "summary": _score_ic_summary([], forward_days, min_n, None, None, 0),
        }

    rows = BacktestRepository(db).find_index_results(
        backtest_id, start_date=start_date, end_date=end_date
    )
    # 迁移前历史行没有此标记，按 True 兼容；新生命周期监控回测会精确记录。
    selection_dates = {
        row.trade_date for row in rows if getattr(row, "selection_rebalanced", True)
    }
    scores_by_date: dict[date, dict[str, float]] = {}
    universe_counts: dict[date, int] = {}
    index_codes: set[str] = set()
    unscored_rows = 0
    for row in rows:
        trade_date = row.trade_date
        if trade_date not in selection_dates:
            continue
        universe_counts[trade_date] = universe_counts.get(trade_date, 0) + 1
        # 缺少 scored 字段的历史/兼容对象按"已评分"处理，保持改造前口径
        if getattr(row, "scored", True) is False:
            unscored_rows += 1
            continue
        score = float(row.signal_score)
        if not math.isfinite(score):
            unscored_rows += 1
            continue
        scores_by_date.setdefault(trade_date, {})[row.index_code] = score
        index_codes.add(row.index_code)

    def summary_of(
        observations: list[dict[str, Any]],
        *,
        selection_windows_non_overlapping: bool = False,
    ) -> dict[str, Any]:
        """按本函数的覆盖度口径汇总观测。"""
        return _score_ic_summary(
            observations,
            forward_days,
            min_n,
            scores_by_date,
            universe_counts,
            unscored_rows,
            already_non_overlapping=selection_windows_non_overlapping,
        )

    if not universe_counts:
        return {"series": [], "summary": summary_of([])}

    # 每个实际选股调仓日都产出观测（即使当天一个资产都没被评分），这样
    # "横截面不足"会以 excluded_low_n_days 的形式显式计数，而不是静默消失。
    observation_dates = sorted(universe_counts)
    window_end = end_date + timedelta(days=FORWARD_LOOKAHEAD_CALENDAR_DAYS)
    bar_repo = IndexDailyBarRepository(db)
    trading_dates = bar_repo.find_all_trading_dates(start_date, window_end)
    if not trading_dates:
        observations = [
            {
                "trade_date": trade_date,
                "ic": None,
                "cross_section_n": 0,
                "status": STATUS_NO_FORWARD,
            }
            for trade_date in observation_dates
        ]
    else:
        bar_map = bar_repo.find_all_date_range(start_date, window_end, sorted(index_codes))
        price_lookup = {
            (code, bar_date): float(row.close_price)
            for (code, bar_date), row in bar_map.items()
            if row.close_price is not None
        }
        forward_returns = build_forward_returns(price_lookup, trading_dates, forward_days)
        observations = []
        for trade_date in observation_dates:
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
                    trade_date, scores_by_date.get(trade_date, {}), per_index, min_n
                )
            )

    # 不能仅因"都是真实调仓日"就认定前瞻窗口不重叠：月度调仓在节假日、
    # 月初/月末切换时，两个实际交易日可能少于 21 个交易日。只要任意两次
    # 有效 IC 观测的间隔小于前瞻期，统计量就回退到保守的重叠样本折算。
    selection_windows_non_overlapping = _selection_windows_are_non_overlapping(
        observations, trading_dates, forward_days
    )
    summary = summary_of(
        observations,
        selection_windows_non_overlapping=selection_windows_non_overlapping,
    )
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


def _score_ic_summary(
    observations: Sequence[Mapping[str, Any]],
    forward_days: int,
    min_n: int,
    scored_by_date: Mapping[date, Mapping[str, float]] | None,
    universe_counts: Mapping[date, int] | None,
    unscored_rows: int,
    *,
    already_non_overlapping: bool = False,
) -> dict[str, Any]:
    """在 :func:`summarize_ic` 之上补充组合分数 IC 的覆盖度诊断。

    ``coverage_ratio`` 按整段区间的总量计算（而不是逐日比例的均值），
    避免评分资产极少的日子把小分母比例放大成误导性的高覆盖。

    组合分数 IC 的观测只取实际选股调仓日；但是否没有前瞻窗口重叠必须按
    实际交易日历验证，不能由调仓频率推断。

    Args:
        observations: IC 观测列表（仅选股调仓日）。
        forward_days: 前瞻交易日数量。
        min_n: 横截面门槛。
        scored_by_date: 每日真正参与评分的资产映射，None 表示无数据。
        universe_counts: 每日落库标的数，None 表示无数据。
        unscored_rows: 未参与评分的行数（含得分非有限的行）。
        already_non_overlapping: 有效 IC 观测的前瞻窗口是否已由实际交易日历
            验证为互不重叠。

    Returns:
        汇总字典。
    """
    summary = summarize_ic(
        observations,
        forward_days,
        min_n,
        already_non_overlapping=already_non_overlapping,
    )
    summary["selection_windows_non_overlapping"] = already_non_overlapping
    scored = scored_by_date or {}
    universe = universe_counts or {}
    scored_total = sum(len(m) for m in scored.values())
    universe_total = sum(universe.values())
    summary["scored_n_avg"] = round(scored_total / len(scored), 1) if scored else None
    summary["universe_n_avg"] = (
        round(universe_total / len(universe), 1) if universe else None
    )
    summary["coverage_ratio"] = (
        round(scored_total / universe_total, 4) if universe_total else None
    )
    summary["unscored_rows"] = unscored_rows
    return summary


def _selection_windows_are_non_overlapping(
    observations: Sequence[Mapping[str, Any]],
    trading_dates: Sequence[date],
    forward_days: int,
) -> bool:
    """按实际交易日历判断有效 IC 观测的前瞻窗口是否互不重叠。"""
    if forward_days <= 1:
        return True

    trading_positions = {trade_date: position for position, trade_date in enumerate(trading_dates)}
    valid_dates = [
        item["trade_date"]
        for item in observations
        if item.get("status") == STATUS_OK and item.get("trade_date") in trading_positions
    ]
    return all(
        trading_positions[current] - trading_positions[previous] >= forward_days
        for previous, current in zip(valid_dates, valid_dates[1:], strict=False)
    )


def calc_factor_correlation_matrix(
    db: Session,
    trade_date: date,
    factor_ids: list[str] | None = None,
    min_n: int = MIN_CROSS_SECTION_N,
    registry: FactorTemplateRegistry | None = None,
) -> dict[str, Any]:
    """计算因子间截面 Rank 相关矩阵。

    对指定日期的全部活跃指数，按**成对交集**计算各因子值之间的 Spearman
    相关系数；因子值由现算服务按各模板默认参数一次算出。

    Args:
        db: SQLAlchemy 同步 Session。
        trade_date: 交易日。
        factor_ids: 要计算的模板 ID 列表，None 表示全部已注册模板。
        min_n: 计算相关系数所需的最小成对样本数。
        registry: 因子模板注册表，None 时使用进程级单例。

    Returns:
        含 factor_ids / matrix / pair_counts / index_count /
        undetermined_pair_count / trade_date 的字典；``index_count`` 为当日
        有任一因子值的指数数量（原始横截面规模）。
    """
    templates = registry or get_factor_template_registry()
    refs = list(factor_ids) if factor_ids else templates.ids()
    instances = [templates.resolve(ref) for ref in refs]
    index_codes = [
        row.index_code for row in BenchmarkIndexRepository(db).find_for_period(trade_date)
    ]

    values_by_factor: dict[str, dict[str, float]] = {}
    index_codes_with_value: set[str] = set()
    if instances and index_codes:
        matrix = FactorComputeService(db, templates).compute_matrix(
            instances, index_codes, [trade_date]
        )
        day = matrix.day(trade_date)
        for instance in instances:
            code_values = {
                code: value
                for (code, ref), value in day.items()
                if ref == instance.instance_id and value is not None
            }
            if code_values:
                values_by_factor[instance.instance_id] = code_values
                index_codes_with_value.update(code_values)

    if len(values_by_factor) < 2:
        return {
            "factor_ids": sorted(values_by_factor),
            "matrix": [],
            "pair_counts": [],
            "index_count": len(index_codes_with_value),
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
        "index_count": len(index_codes_with_value),
        "undetermined_pair_count": undetermined,
        "trade_date": str(trade_date),
    }
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
