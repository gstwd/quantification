"""策略稳健性统计的纯函数模块。

回答"这个回测结果有多依赖历史细节"，而不是"收益有多高"。输入是已落库的
逐日回测序列（组合收益、基准收益、换手、仓位、持仓），输出成本折算、
收益集中度、分段一致性、回撤结构与结构指标。

全部函数为纯函数，不读取数据库、不提交事务，便于单测与在读取路径复用。
指标的时点/口径约定与 ``domain.research.metrics`` 保持一致：
日收益单位为百分数（1.0 表示 1%），年化系数默认 252。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

# 年化系数与绩效指标模块保持一致，避免"汇总一套、稳健性一套"的口径漂移
DEFAULT_TRADING_DAYS_PER_YEAR = 252
# 默认单边交易成本（基点）：1bp = 0.01%，与 v8 验证使用的成本档位对齐
DEFAULT_COST_BPS = 10.0


@dataclass
class StabilityMetrics:
    """单次回测的稳健性指标汇总。

    Attributes:
        cost_bps: 成本折算使用的单边成本（基点）。
        annualized_turnover: 年化单边换手率（倍）。
        cost_drag_pct_per_year: 成本拖累（百分点/年）= 年化换手 × 成本。
        net_cumulative_return_pct: 扣成本后累计收益率（%）。
        net_annualized_return_pct: 扣成本后年化收益率（%）。
        net_sharpe_ratio: 扣成本后年化夏普比率。
        net_excess_return_pct: 扣成本后相对基准的年化超额（百分点），无基准时为 None。
        year_return_share_max: 年度对数收益占比的最大值（0-1，越接近 1 越集中）。
        year_return_share_hhi: 年度对数收益占比的赫芬达尔指数（0-1）。
        best_year: 对数收益最大的年份，无数据时为 None。
        ex_best_year_annualized_return_pct: 剔除最好年份后的年化收益率（%）。
        ex_best_year_sharpe_ratio: 剔除最好年份后的年化夏普比率。
        annual_sharpe_positive_ratio: 夏普为正的自然年占比（0-1）。
        segment_sharpe_positive_ratio: 三段等分区中夏普为正的比例（0-1）。
        best_segment_sharpe: 三段等分区中最好的夏普。
        worst_segment_sharpe: 三段等分区中最差的夏普。
        max_drawdown_pct: 全期最大回撤（%）。
        current_drawdown_pct: 期末回撤（%）。
        current_drawdown_percentile_pct: 期末回撤在逐日回撤分布中的分位（0-100，
            数值越大表示当前回撤相对自身历史越极端）。
        max_drawdown_days: 最长水下（低于前高）持续天数。
        average_exposure: 平均仓位（0-1），无仓位数据时为 None。
        position_concentration: 持仓权重的平均赫芬达尔指数（0-1），无持仓数据时为 None。
    """

    cost_bps: float = DEFAULT_COST_BPS
    annualized_turnover: float = 0.0
    cost_drag_pct_per_year: float = 0.0
    net_cumulative_return_pct: float = 0.0
    net_annualized_return_pct: float = 0.0
    net_sharpe_ratio: float = 0.0
    net_excess_return_pct: float | None = None
    year_return_share_max: float = 0.0
    year_return_share_hhi: float = 0.0
    best_year: int | None = None
    ex_best_year_annualized_return_pct: float = 0.0
    ex_best_year_sharpe_ratio: float = 0.0
    annual_sharpe_positive_ratio: float = 0.0
    segment_sharpe_positive_ratio: float = 0.0
    best_segment_sharpe: float = 0.0
    worst_segment_sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    current_drawdown_percentile_pct: float = 0.0
    max_drawdown_days: int = 0
    average_exposure: float | None = None
    position_concentration: float | None = None


def compute_stability_metrics(
    daily_returns: list[float],
    trade_dates: list[date],
    benchmark_returns: list[float] | None = None,
    turnovers: list[float | None] | None = None,
    exposures: list[float | None] | None = None,
    positions: list[dict[str, float] | None] | None = None,
    cost_bps: float = DEFAULT_COST_BPS,
    trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
) -> StabilityMetrics:
    """从逐日回测序列计算稳健性指标。

    成本口径：``net = gross - 单边换手率 × cost_bps / 100``。系统的
    ``turnover`` 字段是单边换手率（仓位变动绝对值之和 / 2），因此
    ``cost_bps`` 表示每单位单边换手的往返成本（基点）。例如年化单边换手
    13.31 倍、成本 10bp 时，年成本拖累 1.331 个百分点。

    Args:
        daily_returns: 组合日收益率序列（%），按日期升序。
        trade_dates: 与 ``daily_returns`` 等长的交易日序列（升序）。
        benchmark_returns: 基准日收益率序列（%），长度不一致时按无基准处理。
        turnovers: 与 ``daily_returns`` 等长的单边换手率序列，缺失日按 0 处理。
        exposures: 与 ``daily_returns`` 等长的总仓位序列，用于统计平均仓位。
        positions: 与 ``daily_returns`` 等长的持仓权重字典序列，用于统计集中度。
        cost_bps: 单边交易成本（基点），默认 10bp。
        trading_days_per_year: 年化系数，默认 252。

    Returns:
        StabilityMetrics 实例；输入为空时返回零值指标。

    Raises:
        ValueError: ``daily_returns`` 与 ``trade_dates`` 长度不一致时抛出。
    """
    n = len(daily_returns)
    if n == 0:
        return StabilityMetrics(cost_bps=cost_bps)
    if len(trade_dates) != n:
        raise ValueError("daily_returns 与 trade_dates 长度必须一致")

    normalized_turnovers = _align_optional(turnovers, n, 0.0)
    costs = [t * cost_bps / 100.0 for t in normalized_turnovers]
    net_returns = [gross - cost for gross, cost in zip(daily_returns, costs)]

    years = n / trading_days_per_year
    annualized_turnover = sum(normalized_turnovers) / years if years > 0 else 0.0

    net_annualized = _annualized_return(net_returns, trading_days_per_year)
    net_excess: float | None = None
    clean_benchmark = clean_returns_series(benchmark_returns, n)
    if clean_benchmark is not None:
        net_excess = net_annualized - _annualized_return(
            clean_benchmark, trading_days_per_year
        )

    concentration = _yearly_concentration(daily_returns, trade_dates, trading_days_per_year)
    segments = _segment_sharpes(daily_returns, trading_days_per_year)
    drawdown = _drawdown_profile(daily_returns)

    return StabilityMetrics(
        cost_bps=cost_bps,
        annualized_turnover=round(annualized_turnover, 4),
        cost_drag_pct_per_year=round(annualized_turnover * cost_bps / 100.0, 4),
        net_cumulative_return_pct=round(_total_return(net_returns), 4),
        net_annualized_return_pct=round(net_annualized, 4),
        net_sharpe_ratio=_sharpe_ratio(net_returns, trading_days_per_year),
        net_excess_return_pct=round(net_excess, 4) if net_excess is not None else None,
        year_return_share_max=concentration["share_max"],
        year_return_share_hhi=concentration["hhi"],
        best_year=concentration["best_year"],
        ex_best_year_annualized_return_pct=concentration["ex_best_annualized"],
        ex_best_year_sharpe_ratio=concentration["ex_best_sharpe"],
        annual_sharpe_positive_ratio=concentration["sharpe_positive_ratio"],
        segment_sharpe_positive_ratio=segments["positive_ratio"],
        best_segment_sharpe=segments["best"],
        worst_segment_sharpe=segments["worst"],
        max_drawdown_pct=drawdown["max_drawdown_pct"],
        current_drawdown_pct=drawdown["current_drawdown_pct"],
        current_drawdown_percentile_pct=drawdown["current_percentile"],
        max_drawdown_days=drawdown["max_drawdown_days"],
        average_exposure=_mean_optional(exposures, n),
        position_concentration=_position_concentration(positions, n),
    )


def net_return_series(
    daily_returns: list[float],
    turnovers: list[float | None] | None = None,
    cost_bps: float = DEFAULT_COST_BPS,
) -> list[float]:
    """按单边换手率折算扣成本后的日收益序列（%）。

    独立暴露该函数，供生命周期模块在上线后监控中使用同一成本口径，
    避免"回测净口径"与"监控净口径"两套算法。

    Args:
        daily_returns: 组合日收益率序列（%）。
        turnovers: 与 ``daily_returns`` 等长的单边换手率序列，缺失日按 0 处理。
        cost_bps: 单边交易成本（基点）。

    Returns:
        扣成本后的日收益率序列（%），长度与输入一致。
    """
    normalized = _align_optional(turnovers, len(daily_returns), 0.0)
    return [gross - t * cost_bps / 100.0 for gross, t in zip(daily_returns, normalized)]


def clean_returns_series(
    values: list[float | None] | None, length: int
) -> list[float] | None:
    """把可选的收益序列对齐并清洗，任一位置缺失时整体作废。

    基准收益序列只要有一天缺失，后续的超额/Alpha 计算就会被 None 污染；
    此处选择"整体作废"而不是逐日补 0，避免把缺失当成"基准零收益"。

    Args:
        values: 原始序列，可能为 None 或短于目标长度。
        length: 目标长度。

    Returns:
        长度为 ``length`` 的浮点序列；长度不符或存在缺失时返回 None。
    """
    if not values or len(values) < length:
        return None
    cleaned: list[float] = []
    for value in values[:length]:
        if value is None or not math.isfinite(float(value)):
            return None
        cleaned.append(float(value))
    return cleaned


def _align_optional(
    values: list[float | None] | None, length: int, default: float
) -> list[float]:
    """把可选序列对齐到指定长度，缺失或 None 的位置填默认值。

    Args:
        values: 原始序列，可能为 None 或短于目标长度。
        length: 目标长度。
        default: 缺失位置使用的默认值。

    Returns:
        长度为 ``length`` 的浮点序列。
    """
    if not values:
        return [default] * length
    aligned = [default] * length
    for i, value in enumerate(values[:length]):
        if value is not None and math.isfinite(value):
            aligned[i] = float(value)
    return aligned


def _total_return(daily_returns: list[float]) -> float:
    """计算累计收益率（%）。"""
    cumulative = 1.0
    for r in daily_returns:
        cumulative *= 1 + r / 100
    return (cumulative - 1) * 100


def _annualized_return(daily_returns: list[float], trading_days: int) -> float:
    """计算年化收益率（%），交易日不足一年时按比例外推。"""
    if not daily_returns or trading_days <= 0:
        return 0.0
    total = _total_return(daily_returns) / 100
    years = len(daily_returns) / trading_days
    if years <= 0 or total <= -1:
        return 0.0
    return ((1 + total) ** (1 / years) - 1) * 100


def _sharpe_ratio(daily_returns: list[float], trading_days: int) -> float:
    """计算年化夏普比率（全期口径、无风险利率为 0）。"""
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    if std_r <= 0:
        return 0.0
    return round(mean_r / std_r * math.sqrt(trading_days), 4)


def _drawdown_series(daily_returns: list[float]) -> list[float]:
    """计算逐日回撤序列（%），回撤为 0 或负值。"""
    series: list[float] = []
    cumulative = 1.0
    peak = 1.0
    for r in daily_returns:
        cumulative *= 1 + r / 100
        if cumulative > peak:
            peak = cumulative
        series.append((cumulative / peak - 1) * 100)
    return series


def _drawdown_profile(daily_returns: list[float]) -> dict[str, float | int]:
    """汇总回撤结构：最大回撤、期末回撤、期末分位与最长水下天数。

    Returns:
        含 max_drawdown_pct / current_drawdown_pct / current_percentile /
        max_drawdown_days 的字典。
    """
    series = _drawdown_series(daily_returns)
    if not series:
        return {
            "max_drawdown_pct": 0.0,
            "current_drawdown_pct": 0.0,
            "current_percentile": 0.0,
            "max_drawdown_days": 0,
        }
    current = series[-1]
    # 分位含义：历史上有多高比例的日子回撤不比当前更浅（越接近 100 越极端）
    depth_count = sum(1 for dd in series if dd <= current)
    percentile = depth_count / len(series) * 100
    max_days = 0
    streak = 0
    for dd in series:
        if dd < 0:
            streak += 1
            max_days = max(max_days, streak)
        else:
            streak = 0
    return {
        "max_drawdown_pct": round(min(series), 4),
        "current_drawdown_pct": round(current, 4),
        "current_percentile": round(percentile, 2),
        "max_drawdown_days": max_days,
    }


def _yearly_concentration(
    daily_returns: list[float], trade_dates: list[date], trading_days: int
) -> dict[str, float | int | None]:
    """按自然年统计收益集中度与分年度一致性。

    占比基于年度对数收益的绝对值，避免正负年份在占比中相互抵消。

    Returns:
        含 share_max / hhi / best_year / ex_best_annualized / ex_best_sharpe /
        sharpe_positive_ratio 的字典。
    """
    by_year: dict[int, list[float]] = {}
    for ret, day in zip(daily_returns, trade_dates):
        by_year.setdefault(day.year, []).append(ret)
    if not by_year:
        return {
            "share_max": 0.0,
            "hhi": 0.0,
            "best_year": None,
            "ex_best_annualized": 0.0,
            "ex_best_sharpe": 0.0,
            "sharpe_positive_ratio": 0.0,
        }

    log_returns: dict[int, float] = {}
    for year, returns in by_year.items():
        log_returns[year] = sum(math.log(1 + r / 100) for r in returns if r > -100)
    total_abs = sum(abs(v) for v in log_returns.values())
    if total_abs <= 0:
        shares = {year: 0.0 for year in log_returns}
    else:
        shares = {year: abs(v) / total_abs for year, v in log_returns.items()}

    best_year = max(log_returns, key=lambda y: log_returns[y])
    # 剔除最好年份：若结果仍成立，说明收益不依赖单一幸运年份
    remaining: list[float] = []
    for ret, day in zip(daily_returns, trade_dates):
        if day.year != best_year:
            remaining.append(ret)
    positive_years = 0
    for year, returns in by_year.items():
        if _sharpe_ratio(returns, trading_days) > 0:
            positive_years += 1

    return {
        "share_max": round(max(shares.values()), 4),
        "hhi": round(sum(s * s for s in shares.values()), 4),
        "best_year": best_year,
        "ex_best_annualized": round(_annualized_return(remaining, trading_days), 4),
        "ex_best_sharpe": _sharpe_ratio(remaining, trading_days),
        "sharpe_positive_ratio": round(positive_years / len(by_year), 4),
    }


def _segment_sharpes(daily_returns: list[float], trading_days: int) -> dict[str, float]:
    """把全期等分为三段，统计分段一致性与最好/最差段夏普。"""
    n = len(daily_returns)
    if n < 3:
        sharpe = _sharpe_ratio(daily_returns, trading_days)
        return {
            "positive_ratio": 1.0 if sharpe > 0 else 0.0,
            "best": sharpe,
            "worst": sharpe,
        }
    chunk = n // 3
    boundaries = [(i * chunk, n if i == 2 else (i + 1) * chunk) for i in range(3)]
    sharpes = [_sharpe_ratio(daily_returns[start:end], trading_days) for start, end in boundaries]
    positive = sum(1 for s in sharpes if s > 0)
    return {
        "positive_ratio": round(positive / len(sharpes), 4),
        "best": max(sharpes),
        "worst": min(sharpes),
    }


def _mean_optional(values: list[float | None] | None, length: int) -> float | None:
    """计算可选序列的均值，无有效数据时返回 None。"""
    if not values:
        return None
    valid = [float(v) for v in values[:length] if v is not None and math.isfinite(v)]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 4)


def _position_concentration(
    positions: list[dict[str, float] | None] | None, length: int
) -> float | None:
    """计算持仓权重的平均赫芬达尔指数，无持仓数据时返回 None。"""
    if not positions:
        return None
    hhis: list[float] = []
    for item in positions[:length]:
        if not item:
            continue
        weights = [abs(float(w)) for w in item.values() if w]
        total = sum(weights)
        if total <= 0:
            continue
        hhis.append(sum((w / total) ** 2 for w in weights))
    if not hhis:
        return None
    return round(sum(hhis) / len(hhis), 4)
