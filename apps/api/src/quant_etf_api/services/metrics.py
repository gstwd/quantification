"""专业绩效指标计算模块。

提供年化收益、最大回撤、Sharpe/Sortino/Calmar 比率、
Alpha/Beta、信息比率等职业化回测评估指标，以及
滚动窗口（Sharpe/Sortino）与分年度绩效表等时间维度指标。

所有函数均为纯函数，无副作用，输入为日收益率序列。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


def _daily_risk_free_pct(annual_risk_free_rate_pct: float, trading_days: int) -> float:
    """把年化无风险利率（%）折算为每个交易日的等效利率（%）。

    采用复利折算 (1 + rf)^(1/trading_days) - 1，与日收益复利口径一致；
    rf=0 时返回 0，保持历史行为不变。

    Args:
        annual_risk_free_rate_pct: 年化无风险利率（%），如 2.0 表示 2%。
        trading_days: 每年交易日数，默认口径 252。

    Returns:
        日度等效无风险利率（%）。
    """
    if annual_risk_free_rate_pct <= 0 or trading_days <= 0:
        return 0.0
    return ((1 + annual_risk_free_rate_pct / 100) ** (1 / trading_days) - 1) * 100


@dataclass
class RollingMetrics:
    """单一时点滚动窗口绩效指标。

    Attributes:
        sharpe_ratio: 滚动窗口年化夏普比率。
        sortino_ratio: 滚动窗口年化索提诺比率。
        annualized_return_pct: 滚动窗口年化收益率（%）。
        annualized_volatility_pct: 滚动窗口年化波动率（%）。
    """

    sharpe_ratio: float
    sortino_ratio: float
    annualized_return_pct: float
    annualized_volatility_pct: float


@dataclass
class AnnualPerformanceRow:
    """单个自然年的绩效汇总。

    Attributes:
        year: 自然年份。
        trading_days: 该年参与统计的交易日数。
        total_return_pct: 该年累计收益率（%）。
        annualized_return_pct: 该年年化收益率（%）。
        sharpe_ratio: 该年夏普比率。
        sortino_ratio: 该年索提诺比率。
        max_drawdown_pct: 该年最大回撤（%）。
    """

    year: int
    trading_days: int
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float


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
    active_returns: list[float] | None = None,
    annual_risk_free_rate_pct: float = 0.0,
) -> PerformanceMetrics:
    """从日收益率序列计算全部绩效指标。

    指标口径（B11 明确）：
    - 全期口径（含空仓 0 收益日）：累计/年化收益、回撤、夏普、索提诺、
      卡玛、Alpha/Beta/信息比率——衡量策略整体（含择时空仓）的真实风险收益；
    - 持仓期口径（仅持仓日）：胜率、持仓日——空仓日不是"失败日"，不计入胜率分母。

    Args:
        daily_returns: 策略日收益率列表（%）。
        benchmark_returns: 基准日收益率列表（%），需与 daily_returns 长度一致。
        trading_days_per_year: 年化系数，默认 252。
        active_returns: 持仓日收益率列表（%）。提供时胜率基于该序列计算；
            None 时回退到 daily_returns（兼容仅传全期序列的调用方）。
        annual_risk_free_rate_pct: 年化无风险利率（%），默认 0（历史口径）。
            夏普/索提诺按复利折算为日度利率后从日收益中扣除，Alpha 同步扣减。

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
    sharpe = _calc_sharpe_ratio(daily_returns, trading_days_per_year, annual_risk_free_rate_pct)
    sortino = _calc_sortino_ratio(daily_returns, trading_days_per_year, annual_risk_free_rate_pct)
    calmar = _calc_calmar_ratio(annualized_return_pct, max_drawdown_pct)
    # 胜率使用持仓期口径：空仓 0 收益日不参与分母（B11）
    win_returns = active_returns if active_returns else daily_returns
    win_rate_pct = _calc_win_rate(win_returns)
    profit_loss_ratio = _calc_profit_loss_ratio(daily_returns)

    var_95, cvar_95 = _calc_var_cvar(daily_returns)
    max_consecutive_loss = _calc_max_consecutive_loss_days(daily_returns)

    alpha, beta, ir, tracking_error = None, None, None, None
    if benchmark_returns and len(benchmark_returns) == n:
        alpha, beta = _calc_alpha_beta(
            daily_returns,
            benchmark_returns,
            trading_days_per_year,
            annual_risk_free_rate_pct,
        )
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


def _calc_sharpe_ratio(
    daily_returns: list[float],
    trading_days: int,
    annual_risk_free_rate_pct: float = 0.0,
) -> float:
    """计算年化夏普比率（全期口径，含空仓 0 收益日）。

    含空仓日会同时压低日均收益与日波动，是全期策略的真实风险收益度量；
    若只看持仓日会系统性虚高夏普（约 √(持仓日占比) 倍），且与年化/回撤等
    全期指标口径割裂，故 B11 明确保留全期口径。

    Args:
        daily_returns: 日收益率序列（%）。
        trading_days: 年化系数（每年交易日数）。
        annual_risk_free_rate_pct: 年化无风险利率（%），默认 0。

    Returns:
        年化夏普比率，无波动时返回 0。
    """
    if len(daily_returns) < 2:
        return 0.0
    daily_rf = _daily_risk_free_pct(annual_risk_free_rate_pct, trading_days)
    excess = [r - daily_rf for r in daily_returns]
    mean_excess = sum(excess) / len(excess)
    variance = sum((r - mean_excess) ** 2 for r in excess) / (len(excess) - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    if std_r <= 0:
        return 0.0
    return round(mean_excess / std_r * math.sqrt(trading_days), 4)


def _calc_sortino_ratio(
    daily_returns: list[float],
    trading_days: int,
    annual_risk_free_rate_pct: float = 0.0,
) -> float:
    """计算年化索提诺比率（全期口径，含空仓 0 收益日）。

    口径与夏普一致：全期序列、以日度无风险利率为最低目标收益率（MAR），
    分子取超额日收益均值，分母取相对 MAR 的下行偏差（含 0 贡献的样本标准差），
    衡量策略整体的下行风险调整收益。rf=0 时退化为 MAR=0 的历史口径。

    Args:
        daily_returns: 日收益率序列（%）。
        trading_days: 年化系数（每年交易日数）。
        annual_risk_free_rate_pct: 年化无风险利率（%），默认 0。

    Returns:
        年化索提诺比率，无下行波动时返回 0。
    """
    if len(daily_returns) < 2:
        return 0.0
    daily_rf = _daily_risk_free_pct(annual_risk_free_rate_pct, trading_days)
    excess = [r - daily_rf for r in daily_returns]
    mean_excess = sum(excess) / len(excess)
    # 下行偏差：低于 MAR 的部分记负值，其余记 0，保留全样本长度做分母
    downside = [min(r - daily_rf, 0.0) for r in daily_returns]
    variance = sum(d * d for d in downside) / (len(downside) - 1)
    downside_std = math.sqrt(variance) if variance > 0 else 0.0
    if downside_std <= 0:
        return 0.0
    return round(mean_excess / downside_std * math.sqrt(trading_days), 4)


def _calc_calmar_ratio(annualized_return_pct: float, max_drawdown_pct: float) -> float:
    """计算卡玛比率（年化收益 / |最大回撤|）。"""
    if abs(max_drawdown_pct) < 0.01:
        return 0.0
    return round(annualized_return_pct / abs(max_drawdown_pct), 4)


def _calc_win_rate(daily_returns: list[float]) -> float:
    """计算胜率（正收益日占比，%）。

    B11 起由调用方传入持仓日收益序列，空仓 0 收益日不再计入分母；
    传入全期序列时退化为全期口径（兼容旧调用）。
    """
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
    annual_risk_free_rate_pct: float = 0.0,
) -> tuple[float, float]:
    """计算 vs 基准的年化 Alpha(%) 和 Beta。

    Alpha = (策略年化收益 - 无风险利率) - Beta * (基准年化收益 - 无风险利率)
    无风险利率默认为 0（历史口径），传入非零年化利率时按上式扣减。

    Args:
        daily_returns: 策略日收益率序列（%）。
        benchmark_returns: 基准日收益率序列（%）。
        trading_days: 年化系数（每年交易日数）。
        annual_risk_free_rate_pct: 年化无风险利率（%），默认 0。

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
    alpha = (annualized_p - annual_risk_free_rate_pct) - beta * (
        annualized_b - annual_risk_free_rate_pct
    )

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


def compute_rolling_metrics(
    daily_returns: list[float],
    window: int = 252,
    trading_days_per_year: int = 252,
    annual_risk_free_rate_pct: float = 0.0,
) -> list[RollingMetrics | None]:
    """计算滚动窗口夏普/索提诺等指标序列（与全期指标共用同一内部口径）。

    每个交易日取"当日及此前 window-1 个交易日"共 window 个日收益作为样本；
    样本不足完整窗口（回测前段）时对应位置返回 None，前端展示时留空即可。
    滚动指标口径与全期指标完全一致：全期含空仓 0 收益日、按同一
    年化系数与无风险利率折算，避免"汇总口径 A、滚动口径 B"的漂移。

    Args:
        daily_returns: 日收益率序列（%），按日期升序。
        window: 滚动窗口长度（交易日数），默认 252。
        trading_days_per_year: 年化系数，默认 252。
        annual_risk_free_rate_pct: 年化无风险利率（%），默认 0。

    Returns:
        与 daily_returns 等长的列表，前 window-1 个元素为 None。

    Raises:
        ValueError: window 小于 2 时抛出。
    """
    if window < 2:
        raise ValueError("滚动窗口至少需要 2 个交易日")
    n = len(daily_returns)
    result: list[RollingMetrics | None] = [None] * min(window - 1, n)
    for i in range(window - 1, n):
        sample = daily_returns[i - window + 1 : i + 1]
        mean_r = sum(sample) / len(sample)
        variance = sum((r - mean_r) ** 2 for r in sample) / (len(sample) - 1)
        daily_std = math.sqrt(variance) if variance > 0 else 0.0
        result.append(
            RollingMetrics(
                sharpe_ratio=_calc_sharpe_ratio(
                    sample, trading_days_per_year, annual_risk_free_rate_pct
                ),
                sortino_ratio=_calc_sortino_ratio(
                    sample, trading_days_per_year, annual_risk_free_rate_pct
                ),
                annualized_return_pct=round(
                    _calc_annualized_return(sample, trading_days_per_year), 2
                ),
                annualized_volatility_pct=round(daily_std * math.sqrt(trading_days_per_year), 2),
            )
        )
    return result


def compute_annual_breakdown(
    daily_returns: list[float],
    trade_dates: list[date],
    trading_days_per_year: int = 252,
    annual_risk_free_rate_pct: float = 0.0,
) -> list[AnnualPerformanceRow]:
    """按自然年切分日收益序列，生成逐年绩效表（分年度统计）。

    每年独立按全期口径计算累计/年化收益、夏普、索提诺与最大回撤；
    未满一年的自然年（如回测首尾年）同样按交易日数/252 折算年化，
    与全期年化口径保持一致，交易天数列用于辅助判断样本充分性。

    Args:
        daily_returns: 日收益率序列（%），按日期升序。
        trade_dates: 与 daily_returns 等长的交易日序列（升序）。
        trading_days_per_year: 年化系数，默认 252。
        annual_risk_free_rate_pct: 年化无风险利率（%），默认 0。

    Returns:
        按年份升序排列的分年度绩效行；任一输入为空时返回空列表。

    Raises:
        ValueError: daily_returns 与 trade_dates 长度不一致时抛出。
    """
    if len(daily_returns) != len(trade_dates):
        raise ValueError("daily_returns 与 trade_dates 长度必须一致")
    if not daily_returns:
        return []

    rows: list[AnnualPerformanceRow] = []
    by_year: dict[int, list[float]] = {}
    for ret, d in zip(daily_returns, trade_dates):
        by_year.setdefault(d.year, []).append(ret)
    for year in sorted(by_year):
        year_returns = by_year[year]
        if not year_returns:
            continue
        max_dd, _ = _calc_max_drawdown_details(year_returns)
        rows.append(
            AnnualPerformanceRow(
                year=year,
                trading_days=len(year_returns),
                total_return_pct=round(_calc_total_return(year_returns), 2),
                annualized_return_pct=round(
                    _calc_annualized_return(year_returns, trading_days_per_year), 2
                ),
                sharpe_ratio=_calc_sharpe_ratio(
                    year_returns, trading_days_per_year, annual_risk_free_rate_pct
                ),
                sortino_ratio=_calc_sortino_ratio(
                    year_returns, trading_days_per_year, annual_risk_free_rate_pct
                ),
                max_drawdown_pct=round(max_dd, 2),
            )
        )
    return rows
