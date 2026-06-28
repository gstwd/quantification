"""专业绩效指标计算模块。

提供年化收益、最大回撤、Sharpe/Sortino/Calmar 比率、
Alpha/Beta、信息比率等职业化回测评估指标。

所有函数均为纯函数，无副作用，输入为日收益率序列。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class PerformanceMetrics:
    """回测绩效指标汇总。

    Attributes:
        total_return_pct: 累计收益率（%）。
        annualized_return_pct: 年化收益率（%）。
        max_drawdown_pct: 最大回撤（%）。
        max_drawdown_days: 最大回撤持续天数。
        sharpe_ratio: 年化夏普比率。
        sortino_ratio: 年化索提诺比率。
        calmar_ratio: 年化卡玛比率。
        win_rate_pct: 胜率（正收益日占比，%）。
        profit_loss_ratio: 盈亏比（平均盈利 / 平均亏损的绝对值）。
        alpha: vs 基准的年化 Alpha（%），无基准时为 None。
        beta: vs 基准的 Beta 系数，无基准时为 None。
        information_ratio: 信息比率，无基准时为 None。
        tracking_error_pct: 年化跟踪误差（%），无基准时为 None。
    """

    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    max_drawdown_days: int
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    win_rate_pct: float
    profit_loss_ratio: float | None
    var_95_pct: float = 0.0
    cvar_95_pct: float = 0.0
    max_consecutive_loss_days: int = 0
    alpha: float | None = None
    beta: float | None = None
    information_ratio: float | None = None
    tracking_error_pct: float | None = None


def compute_performance_metrics(
    daily_returns: list[float],
    benchmark_returns: list[float] | None = None,
    trading_days_per_year: int = 252,
) -> PerformanceMetrics:
    """从日收益率序列计算全部绩效指标。

    Args:
        daily_returns: 策略日收益率列表（%）。
        benchmark_returns: 基准日收益率列表（%），需与 daily_returns 长度一致。
        trading_days_per_year: 年化系数，默认 252。

    Returns:
        PerformanceMetrics 实例。
    """
    n = len(daily_returns)
    if n == 0:
        return PerformanceMetrics(
            total_return_pct=0.0,
            annualized_return_pct=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_days=0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            win_rate_pct=0.0,
            profit_loss_ratio=None,
        )

    total_return_pct = _calc_total_return(daily_returns)
    annualized_return_pct = _calc_annualized_return(daily_returns, trading_days_per_year)
    max_drawdown_pct, max_drawdown_days = _calc_max_drawdown_details(daily_returns)
    sharpe = _calc_sharpe_ratio(daily_returns, trading_days_per_year)
    sortino = _calc_sortino_ratio(daily_returns, trading_days_per_year)
    calmar = _calc_calmar_ratio(annualized_return_pct, max_drawdown_pct)
    win_rate_pct = _calc_win_rate(daily_returns)
    profit_loss_ratio = _calc_profit_loss_ratio(daily_returns)

    var_95, cvar_95 = _calc_var_cvar(daily_returns)
    max_consecutive_loss = _calc_max_consecutive_loss_days(daily_returns)

    alpha, beta, ir, tracking_error = None, None, None, None
    if benchmark_returns and len(benchmark_returns) == n:
        alpha, beta = _calc_alpha_beta(daily_returns, benchmark_returns, trading_days_per_year)
        ir, tracking_error = _calc_information_ratio(
            daily_returns, benchmark_returns, trading_days_per_year
        )

    return PerformanceMetrics(
        total_return_pct=round(total_return_pct, 2),
        annualized_return_pct=round(annualized_return_pct, 2),
        max_drawdown_pct=round(max_drawdown_pct, 2),
        max_drawdown_days=max_drawdown_days,
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        calmar_ratio=round(calmar, 4),
        win_rate_pct=round(win_rate_pct, 2),
        profit_loss_ratio=round(profit_loss_ratio, 2) if profit_loss_ratio is not None else None,
        var_95_pct=round(var_95, 2),
        cvar_95_pct=round(cvar_95, 2),
        max_consecutive_loss_days=max_consecutive_loss,
        alpha=round(alpha, 2) if alpha is not None else None,
        beta=round(beta, 2) if beta is not None else None,
        information_ratio=round(ir, 4) if ir is not None else None,
        tracking_error_pct=round(tracking_error, 2) if tracking_error is not None else None,
    )


def _calc_total_return(daily_returns: list[float]) -> float:
    """计算累计收益率（%）。"""
    cumulative = 1.0
    for r in daily_returns:
        cumulative *= 1 + r / 100
    return (cumulative - 1) * 100


def _calc_annualized_return(daily_returns: list[float], trading_days: int) -> float:
    """计算年化收益率（%）。"""
    total = _calc_total_return(daily_returns) / 100
    years = len(daily_returns) / trading_days
    if years <= 0:
        return 0.0
    return ((1 + total) ** (1 / years) - 1) * 100


def _calc_max_drawdown_details(daily_returns: list[float]) -> tuple[float, int]:
    """计算最大回撤（%）及最大回撤持续天数。

    Returns:
        (max_drawdown_pct, max_drawdown_days) 元组。
    """
    cumulative = 1.0
    peak = 1.0
    max_dd = 0.0
    max_dd_days = 0
    current_dd_days = 0

    for r in daily_returns:
        cumulative *= 1 + r / 100
        if cumulative > peak:
            peak = cumulative
            current_dd_days = 0
        else:
            current_dd_days += 1
            dd = (cumulative / peak - 1) * 100
            if dd < max_dd:
                max_dd = dd
            if current_dd_days > max_dd_days:
                max_dd_days = current_dd_days

    return max_dd, max_dd_days


def _calc_sharpe_ratio(daily_returns: list[float], trading_days: int) -> float:
    """计算年化夏普比率。"""
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    if std_r <= 0:
        return 0.0
    return round(mean_r / std_r * math.sqrt(trading_days), 4)


def _calc_sortino_ratio(daily_returns: list[float], trading_days: int) -> float:
    """计算年化索提诺比率（仅用下行标准差）。"""
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    # 下行偏差：仅计入低于 0 的收益
    downside = [min(r, 0.0) for r in daily_returns]
    variance = sum((d - 0) ** 2 for d in downside) / (len(downside) - 1)
    downside_std = math.sqrt(variance) if variance > 0 else 0.0
    if downside_std <= 0:
        return 0.0
    return round(mean_r / downside_std * math.sqrt(trading_days), 4)


def _calc_calmar_ratio(annualized_return_pct: float, max_drawdown_pct: float) -> float:
    """计算卡玛比率（年化收益 / |最大回撤|）。"""
    if abs(max_drawdown_pct) < 0.01:
        return 0.0
    return round(annualized_return_pct / abs(max_drawdown_pct), 4)


def _calc_win_rate(daily_returns: list[float]) -> float:
    """计算胜率（正收益日占比，%）。"""
    if not daily_returns:
        return 0.0
    return sum(1 for r in daily_returns if r > 0) / len(daily_returns) * 100


def _calc_profit_loss_ratio(daily_returns: list[float]) -> float | None:
    """计算盈亏比（平均盈利 / 平均亏损的绝对值）。"""
    gains = [r for r in daily_returns if r > 0]
    losses = [r for r in daily_returns if r < 0]
    if not losses:
        return None
    avg_gain = sum(gains) / len(gains) if gains else 0.0
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return None
    return avg_gain / abs(avg_loss)


def _calc_alpha_beta(
    daily_returns: list[float],
    benchmark_returns: list[float],
    trading_days: int,
) -> tuple[float, float]:
    """计算 vs 基准的年化 Alpha(%) 和 Beta。

    Alpha = (策略年化收益 - 无风险利率) - Beta * (基准年化收益 - 无风险利率)
    无风险利率简化为 0。

    Returns:
        (alpha, beta) 元组。
    """
    n = len(daily_returns)
    if n < 2:
        return 0.0, 0.0

    mean_p = sum(daily_returns) / n
    mean_b = sum(benchmark_returns) / n

    # Beta = Cov(p, b) / Var(b)
    cov = sum((daily_returns[i] - mean_p) * (benchmark_returns[i] - mean_b) for i in range(n)) / (
        n - 1
    )
    var_b = sum((r - mean_b) ** 2 for r in benchmark_returns) / (n - 1)
    beta = cov / var_b if var_b > 0 else 0.0

    # 年化 Alpha
    annualized_p = _calc_annualized_return(daily_returns, trading_days)
    annualized_b = _calc_annualized_return(benchmark_returns, trading_days)
    alpha = annualized_p - beta * annualized_b

    return alpha, beta


def _calc_information_ratio(
    daily_returns: list[float],
    benchmark_returns: list[float],
    trading_days: int,
) -> tuple[float, float]:
    """计算信息比率和年化跟踪误差(%)。

    Returns:
        (information_ratio, tracking_error_pct) 元组。
    """
    n = len(daily_returns)
    if n < 2:
        return 0.0, 0.0

    # 超额收益序列
    excess = [daily_returns[i] - benchmark_returns[i] for i in range(n)]
    mean_excess = sum(excess) / n
    variance = sum((e - mean_excess) ** 2 for e in excess) / (n - 1)
    tracking_error_daily = math.sqrt(variance) if variance > 0 else 0.0
    tracking_error_annual = tracking_error_daily * math.sqrt(trading_days)

    if tracking_error_annual <= 0:
        return 0.0, 0.0

    annualized_excess = _calc_annualized_return(excess, trading_days)
    ir = annualized_excess / tracking_error_annual

    return ir, tracking_error_annual


def _calc_var_cvar(daily_returns: list[float], confidence: float = 0.95) -> tuple[float, float]:
    """计算历史模拟法的 VaR 和 CVaR（%）。

    Args:
        daily_returns: 日收益率序列（%）。
        confidence: 置信水平，默认 0.95。

    Returns:
        (var_pct, cvar_pct) 元组，均为正数表示损失。
    """
    if len(daily_returns) < 5:
        return 0.0, 0.0
    sorted_returns = sorted(daily_returns)
    idx = int(len(sorted_returns) * (1 - confidence))
    var_val = sorted_returns[idx] if idx < len(sorted_returns) else sorted_returns[-1]
    tail = [r for r in sorted_returns if r <= var_val]
    cvar_val = sum(tail) / len(tail) if tail else var_val
    # 负收益 → 正损失
    return round(abs(var_val), 2), round(abs(cvar_val), 2)


def _calc_max_consecutive_loss_days(daily_returns: list[float]) -> int:
    """计算最大连续亏损天数。

    Args:
        daily_returns: 日收益率序列（%）。

    Returns:
        最大连续亏损天数。
    """
    max_streak = 0
    current_streak = 0
    for r in daily_returns:
        if r < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak
