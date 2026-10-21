"""研究诊断纯函数：按因子 / 信号值分桶的未来 N 日收益统计。

研究中反复手写的诊断是"把因子值切成若干桶，看各桶之后 N 日的平均收益、胜率与
显著性，再看前后两段是否一致"。本模块把这一口径固化为纯函数，供 CLI 与服务层复用：

- 前瞻收益按执行口径构造（与回测执行模型同名）：
  ``t_plus_1_open`` 为 T 日收盘出信号、T+1 开盘成交，收益 ``open[T+1+N] / open[T+1] − 1``；
  ``t_plus_1_close`` 为 T+1 收盘成交，收益 ``close[T+1+N] / close[T+1] − 1``；
  ``close_to_close`` 为 ``close[T+N] / close[T] − 1``，与因子 IC / 研究台 fwd 同口径。
- 分桶边界要么由调用方给定内部切点（自动补 ±inf），要么按全样本分位数等分；
  分段一致性检验沿用全样本边界，保证两段的桶含义相同。
- ``N > 1`` 时相邻观测的前瞻窗口重叠，``t_stat`` 按 ``effective_n = n // N`` 折算
  （与 ``factors.evaluation.summarize_ic`` 同口径），同时给出未折算的 ``t_stat_raw``。

不读取数据库、不依赖服务层；数据由调用方以 pandas 序列传入。
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

# 前瞻收益口径
BASIS_T1_OPEN = "t_plus_1_open"
BASIS_T1_CLOSE = "t_plus_1_close"
BASIS_CLOSE_TO_CLOSE = "close_to_close"
RETURN_BASES = (BASIS_T1_OPEN, BASIS_T1_CLOSE, BASIS_CLOSE_TO_CLOSE)
# 单桶样本数低于该值时统计量不可靠，仍输出但标记 low_sample
MIN_BUCKET_SAMPLES = 30
# 全样本因子分布展示的分位点
_DISTRIBUTION_QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


def _num(value: Any, digits: int = 4) -> float | None:
    """转为 JSON 安全的 float 并保留小数（NaN / inf → None）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman 秩相关；样本 < 3 或常数输入时返回 None。"""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    # 常数序列的秩相关未定义并会告警，属于预期情况
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        corr, _ = spearmanr(xs, ys)
    return _num(corr)


def forward_returns(
    open_: pd.Series,
    close: pd.Series,
    horizon: int,
    basis: str = BASIS_T1_OPEN,
) -> pd.Series:
    """按执行口径构造每个交易日的未来 N 日收益（百分比）。

    序列按交易日索引升序；位移按行进行（即按该指数自身的交易日），缺价位置为 NaN，
    行情末端不足前瞻窗口的样本自然为 NaN。

    Args:
        open_: 开盘价序列。
        close: 收盘价序列（索引与 ``open_`` 一致）。
        horizon: 持有交易日数 N，需 ≥ 1。
        basis: 收益口径，见 ``RETURN_BASES``。

    Returns:
        T 日 → 未来 N 日收益（%）。

    Raises:
        ValueError: horizon < 1 或 basis 未知时抛出。
    """
    if horizon < 1:
        raise ValueError("持有期 horizon 必须 ≥ 1 个交易日")
    if basis == BASIS_T1_OPEN:
        price, entry_shift = open_, 1
    elif basis == BASIS_T1_CLOSE:
        price, entry_shift = close, 1
    elif basis == BASIS_CLOSE_TO_CLOSE:
        price, entry_shift = close, 0
    else:
        raise ValueError(f"未知的收益口径：{basis}，可选值为 {', '.join(RETURN_BASES)}")
    price = price.astype(float).where(price > 0)
    entry = price.shift(-entry_shift)
    exit_ = price.shift(-(entry_shift + horizon))
    return (exit_ / entry - 1) * 100


def bucket_edges(
    values: pd.Series,
    *,
    cut_points: Sequence[float] | None = None,
    quantiles: int | None = None,
) -> list[float]:
    """确定分桶边界（含 ±inf 外边界）。

    Args:
        values: 因子值（NaN 忽略）。
        cut_points: 内部切点（升序、去重后使用），与 ``quantiles`` 二选一。
        quantiles: 按全样本分位数等分的桶数（≥ 2）。

    Returns:
        升序边界列表，首尾为 ±inf；分位切点重合时自动合并（桶数可能少于请求值）。

    Raises:
        ValueError: 两种方式都未给或都给、桶数非法、或有效样本不足时抛出。
    """
    if (cut_points is None) == (quantiles is None):
        raise ValueError("分桶方式必须二选一：给定切点（edges）或分位数桶数（quantiles）")
    if cut_points is not None:
        inner = sorted({float(x) for x in cut_points if math.isfinite(float(x))})
        if not inner:
            raise ValueError("分桶切点不能为空")
    else:
        if quantiles is None or quantiles < 2:
            raise ValueError("分位数桶数必须 ≥ 2")
        valid = values.dropna().astype(float)
        if len(valid) < quantiles:
            raise ValueError(f"有效因子值只有 {len(valid)} 个，不足以切成 {quantiles} 桶")
        probs = [i / quantiles for i in range(1, quantiles)]
        inner = sorted({float(v) for v in valid.quantile(probs).to_numpy()})
    return [-math.inf, *inner, math.inf]


def _edge_text(value: float) -> str:
    """边界值的紧凑文本（±inf 写作 ±inf）。"""
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return f"{value:g}"


def bucket_labels(edges: Sequence[float]) -> list[str]:
    """按左开右闭区间生成桶标签，如 ``(-inf, 0]``、``(0, 0.5]``、``(1.5, inf)``。"""
    labels: list[str] = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=False):
        right = ")" if math.isinf(upper) else "]"
        labels.append(f"({_edge_text(lower)}, {_edge_text(upper)}{right}")
    return labels


def assign_buckets(values: pd.Series, edges: Sequence[float]) -> pd.Series:
    """把因子值映射到桶序号（0 起，左开右闭；NaN 映射为 NaN）。"""
    codes = pd.cut(values.astype(float), list(edges), labels=False, right=True)
    return pd.Series(codes, index=values.index, dtype=float)


def _return_stats(returns: pd.Series, horizon: int, total: int) -> dict[str, Any]:
    """单桶单持有期的收益统计。

    Args:
        returns: 该桶内的前瞻收益（%，已去 NaN）。
        horizon: 持有交易日数（决定重叠折算与年化）。
        total: 该持有期全部有效样本数（计算占比）。

    Returns:
        n / share / mean_pct / median_pct / std_pct / annualized_pct / hit_rate /
        effective_n / t_stat / t_stat_raw / low_sample。
    """
    n = len(returns)
    stats: dict[str, Any] = {
        "n": n,
        "share": _num(n / total) if total else None,
        "mean_pct": None,
        "median_pct": None,
        "std_pct": None,
        "annualized_pct": None,
        "hit_rate": None,
        "effective_n": max(n // horizon, 1) if n else 0,
        "t_stat": None,
        "t_stat_raw": None,
        "low_sample": n < MIN_BUCKET_SAMPLES,
    }
    if not n:
        return stats
    mean = float(returns.mean())
    std = float(returns.std(ddof=1)) if n > 1 else math.nan
    stats.update(
        {
            "mean_pct": _num(mean),
            "median_pct": _num(returns.median()),
            "std_pct": _num(std),
            "annualized_pct": _num(mean * 252 / horizon),
            "hit_rate": _num(float((returns > 0).mean())),
        }
    )
    if n > 1 and std > 0:
        stats["t_stat_raw"] = _num(mean / std * math.sqrt(n), 3)
        stats["t_stat"] = _num(mean / std * math.sqrt(stats["effective_n"]), 3)
    return stats


def bucket_table(
    buckets: pd.Series,
    returns: pd.Series,
    edges: Sequence[float],
    horizon: int,
) -> dict[str, Any]:
    """计算一个样本段在单一持有期下的分桶收益表。

    Args:
        buckets: 桶序号序列（``assign_buckets`` 的输出）。
        returns: 同索引的前瞻收益（%）。
        edges: 分桶边界（决定桶数与标签）。
        horizon: 持有交易日数。

    Returns:
        ``{"n", "mean_pct", "buckets": [...], "spread_pct", "monotonicity"}``：
        每桶含 label / lower / upper 与收益统计及相对全样本均值的 ``excess_pct``；
        ``spread_pct`` 为最高桶减最低桶（均有样本时）的均值差；``monotonicity`` 为
        桶序号与桶均值的 Spearman 相关（≥ 3 个非空桶时）。
    """
    frame = pd.DataFrame({"bucket": buckets, "ret": returns}).dropna()
    total = len(frame)
    overall = float(frame["ret"].mean()) if total else math.nan
    labels = bucket_labels(edges)
    rows: list[dict[str, Any]] = []
    for idx, label in enumerate(labels):
        part = frame.loc[frame["bucket"] == idx, "ret"]
        stats = _return_stats(part, horizon, total)
        stats["excess_pct"] = (
            _num(stats["mean_pct"] - overall) if stats["mean_pct"] is not None else None
        )
        rows.append(
            {
                "label": label,
                "lower": _num(edges[idx], 6),
                "upper": _num(edges[idx + 1], 6),
                **stats,
            }
        )
    filled = [(i, r["mean_pct"]) for i, r in enumerate(rows) if r["mean_pct"] is not None]
    spread = _num(filled[-1][1] - filled[0][1]) if len(filled) >= 2 else None
    monotonicity = (
        _spearman([i for i, _ in filled], [m for _, m in filled]) if len(filled) >= 3 else None
    )
    return {
        "n": total,
        "mean_pct": _num(overall),
        "buckets": rows,
        "spread_pct": spread,
        "monotonicity": monotonicity,
    }


def split_halves(index: pd.DatetimeIndex) -> pd.Timestamp | None:
    """按样本交易日数对半切分，返回后半段首日（样本 < 2 时返回 None）。"""
    if len(index) < 2:
        return None
    ordered = index.sort_values()
    return pd.Timestamp(ordered[len(ordered) // 2])


def segment_consistency(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    """比较两个样本段的分桶表是否给出一致结论。

    Args:
        first: 前段 ``bucket_table`` 结果。
        second: 后段 ``bucket_table`` 结果。

    Returns:
        ``rank_corr``：两段桶均值的 Spearman 相关（两段都有均值的桶 ≥ 3 时）；
        ``excess_sign_agreement``：两段超额（桶均值 − 段均值）同号的桶占比；
        ``spread_same_sign``：两段高低桶价差是否同号；``paired_buckets``：参与比较的桶数。
    """
    pairs = [
        (a, b)
        for a, b in zip(first["buckets"], second["buckets"], strict=False)
        if a["mean_pct"] is not None and b["mean_pct"] is not None
    ]
    excess_pairs = [
        (a["excess_pct"], b["excess_pct"])
        for a, b in pairs
        if a["excess_pct"] is not None and b["excess_pct"] is not None
    ]
    agree = (
        _num(sum(1 for x, y in excess_pairs if x * y > 0) / len(excess_pairs))
        if excess_pairs
        else None
    )
    s1, s2 = first.get("spread_pct"), second.get("spread_pct")
    return {
        "paired_buckets": len(pairs),
        "rank_corr": _spearman(
            [a["mean_pct"] for a, _ in pairs], [b["mean_pct"] for _, b in pairs]
        ),
        "excess_sign_agreement": agree,
        "spread_same_sign": (s1 * s2 > 0) if s1 is not None and s2 is not None else None,
    }


def factor_distribution(values: pd.Series) -> dict[str, Any]:
    """全样本因子值分布摘要（帮助选择分桶切点）。"""
    valid = values.dropna().astype(float)
    if valid.empty:
        return {"n": 0}
    result: dict[str, Any] = {
        "n": len(valid),
        "min": _num(valid.min(), 6),
        "max": _num(valid.max(), 6),
        "mean": _num(valid.mean(), 6),
    }
    for q in _DISTRIBUTION_QUANTILES:
        result[f"p{int(q * 100):02d}"] = _num(valid.quantile(q), 6)
    return result


def bucket_forward_returns(
    factor: pd.Series,
    returns_by_horizon: Mapping[int, pd.Series],
    *,
    cut_points: Sequence[float] | None = None,
    quantiles: int | None = None,
    split_at: pd.Timestamp | None = None,
    split_halves_flag: bool = False,
) -> dict[str, Any]:
    """按因子值分桶统计未来 N 日收益（研究诊断主入口，纯函数）。

    Args:
        factor: T 日因子值序列（DatetimeIndex）。
        returns_by_horizon: 持有期 N → 同索引口径的前瞻收益（%，``forward_returns`` 输出）。
        cut_points: 内部切点，与 ``quantiles`` 二选一。
        quantiles: 分位数桶数，与 ``cut_points`` 二选一。
        split_at: 分段日（后段首日，含）；给定时输出前后两段与一致性。
        split_halves_flag: True 且未给 ``split_at`` 时按样本交易日数对半切分。

    Returns:
        ``{"bucketing", "distribution", "tables", "split_at"}``；``tables`` 每个持有期一项，
        含 ``full``（全样本分桶表）与可选的 ``segments`` / ``consistency``。

    Raises:
        ValueError: 分桶参数非法或有效样本不足时抛出。
    """
    factor = factor.astype(float).sort_index()
    edges = bucket_edges(factor, cut_points=cut_points, quantiles=quantiles)
    buckets = assign_buckets(factor, edges)
    split = split_at
    if split is None and split_halves_flag:
        split = split_halves(pd.DatetimeIndex(factor.dropna().index))
    tables: list[dict[str, Any]] = []
    for horizon in sorted(returns_by_horizon):
        ret = returns_by_horizon[horizon].reindex(factor.index)
        entry: dict[str, Any] = {
            "horizon": int(horizon),
            "full": bucket_table(buckets, ret, edges, horizon),
        }
        if split is not None:
            before = factor.index < split
            first = bucket_table(buckets[before], ret[before], edges, horizon)
            second = bucket_table(buckets[~before], ret[~before], edges, horizon)
            entry["segments"] = [
                {"label": "first", **_segment_range(factor.index[before]), **first},
                {"label": "second", **_segment_range(factor.index[~before]), **second},
            ]
            entry["consistency"] = segment_consistency(first, second)
        tables.append(entry)
    return {
        "bucketing": {
            "mode": "edges" if cut_points is not None else "quantiles",
            "requested_quantiles": quantiles,
            "edges": [_num(e, 6) for e in edges[1:-1]],
            "labels": bucket_labels(edges),
        },
        "distribution": factor_distribution(factor),
        "split_at": split.date().isoformat() if split is not None else None,
        "tables": tables,
    }


def _segment_range(index: pd.Index) -> dict[str, str | None]:
    """样本段的起止日期。"""
    if len(index) == 0:
        return {"start": None, "end": None}
    return {
        "start": pd.Timestamp(index.min()).date().isoformat(),
        "end": pd.Timestamp(index.max()).date().isoformat(),
    }


def yearly_ic(series: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把 IC 序列按自然年聚合（均值、正值占比、观测数），用于判断 IC 的跨年稳定性。

    Args:
        series: ``analyze_ic`` 输出的 ``series``（每项含 trade_date 与 ic）。

    Returns:
        按年升序的 ``{"year", "ic_mean", "positive_ratio", "count"}`` 列表。
    """
    by_year: dict[int, list[float]] = {}
    for item in series:
        ic = item.get("ic")
        if ic is None:
            continue
        year = int(str(item["trade_date"])[:4])
        by_year.setdefault(year, []).append(float(ic))
    return [
        {
            "year": year,
            "ic_mean": _num(np.mean(values)),
            "positive_ratio": _num(sum(1 for v in values if v > 0) / len(values)),
            "count": len(values),
        }
        for year, values in sorted(by_year.items())
    ]
