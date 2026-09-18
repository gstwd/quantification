"""策略生命周期诊断的纯函数模块。

生命周期模块只关注"策略上线之后发生了什么"，但它需要在上线时冻结一份
研究期分布作为参照系，否则上线初期的少量数据无法判断异常（第二篇方法论
文档第十一节的 Expectation Gap）。

本模块提供三件事：
1. 从研究期收益序列构建可冻结的分布快照（``build_baseline_distribution``）；
2. 把上线后的表现填回该分布，得到分位与期望差
   （``evaluate_against_baseline``）；
3. 按"阈值全部取自该策略自身历史分布"的原则给出健康等级与建议动作
   （``assess_health``）。

全部为纯函数：不读数据库、不改状态，状态变更只由人工操作触发。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from quant_etf_api.domain.research.stability import clean_returns_series

DEFAULT_TRADING_DAYS_PER_YEAR = 252
# 监控窗口定义（交易日）：1M/3M/6M/12M/24M，样本不足的窗口自动跳过
WINDOW_DAYS: dict[str, int] = {
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "12m": 252,
    "24m": 504,
}
# 分布快照中记录的分位点
PERCENTILE_POINTS: tuple[int, ...] = (5, 10, 25, 50, 75, 90, 95)
# 健康等级
HEALTH_HEALTHY = "HEALTHY"
HEALTH_WATCH = "WATCH"
HEALTH_WARNING = "WARNING"
HEALTH_CRITICAL = "CRITICAL"
# 样本不足以覆盖任何监控窗口：**不能**记成 HEALTHY——"没算出问题"与"没有问题"
# 是两回事，后者需要证据
HEALTH_UNKNOWN = "UNKNOWN"
# 诊断结论
DIAGNOSIS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
DIAGNOSIS_NORMAL = "NORMAL"
DIAGNOSIS_DRAWDOWN_EXTREME = "DRAWDOWN_EXTREME"
DIAGNOSIS_ALPHA_DECAY = "ALPHA_DECAY"
# 建议动作
ACTION_KEEP = "KEEP"
ACTION_WATCH = "WATCH"
ACTION_REDUCE_RISK = "REDUCE_RISK"
ACTION_RESEARCH = "RESEARCH"
# IC 衰减判定：每个半段所需的最小观测数，以及确认衰减所需的显著性倍数。
# 单因子 IC 序列通常噪声主导，仅比较前后半段均值（second < first）会有一半
# 概率把噪声判成衰减，因此要求差值超过 2 倍标准误。
IC_DECAY_MIN_HALF_N = 20
IC_DECAY_SIGMA = 2.0


@dataclass
class HealthAssessment:
    """策略健康诊断结果。

    Attributes:
        health_level: 健康等级（HEALTHY/WATCH/WARNING/CRITICAL/UNKNOWN，UNKNOWN
            表示监控样本还不足以覆盖任何窗口）。
        diagnosis: 诊断结论（NORMAL/DRAWDOWN_EXTREME/ALPHA_DECAY/INSUFFICIENT_DATA）。
        recommended_action: 建议动作（KEEP/WATCH/REDUCE_RISK/RESEARCH）。
        reasons: 触发该结论的具体依据，逐条中文说明。
    """

    health_level: str
    diagnosis: str
    recommended_action: str
    reasons: list[str] = field(default_factory=list)


def build_baseline_distribution(
    daily_returns: list[float],
    benchmark_returns: list[float] | None,
    trade_dates: list[date],
) -> dict:
    """从研究期收益序列构建可冻结的分布快照。

    快照包含各监控窗口的滚动超额收益分布、滚动夏普分布与逐日回撤分布，
    上线后所有分位判定都以该快照为参照，不使用全局写死的阈值。

    Args:
        daily_returns: 研究期组合日收益率序列（%）。
        benchmark_returns: 研究期基准日收益率序列（%），可为 None。
        trade_dates: 与研究期收益等长的交易日序列。

    Returns:
        可 JSON 序列化的分布字典，含 windows / drawdown_pct / period / sample_days。
    """
    n = len(daily_returns)
    # 基准序列存在缺失时整段作废：缺失日不能被当成"基准零收益"
    benchmark_returns = clean_returns_series(benchmark_returns, n) if n else None
    snapshot: dict = {
        "period": {
            "start": trade_dates[0].isoformat() if trade_dates else None,
            "end": trade_dates[-1].isoformat() if trade_dates else None,
        },
        "sample_days": n,
        "windows": {},
    }
    if n == 0:
        return snapshot

    drawdowns = _drawdown_series(daily_returns)
    snapshot["drawdown_pct"] = _percentile_table([abs(d) for d in drawdowns])
    snapshot["sharpe_full_period"] = _annualized_sharpe(daily_returns)

    for label, days in WINDOW_DAYS.items():
        if n < days:
            continue
        window_excess = _rolling_excess_windows(daily_returns, benchmark_returns, days)
        window_sharpe = _rolling_sharpes(daily_returns, days)
        snapshot["windows"][label] = {
            "days": days,
            "n_observations": len(window_excess),
            "excess_return_pct": _percentile_table(window_excess),
            "sharpe_ratio": _percentile_table(window_sharpe),
        }
    return snapshot


def evaluate_against_baseline(
    daily_returns: list[float],
    benchmark_returns: list[float] | None,
    baseline: dict,
) -> dict:
    """把上线后的表现填回研究期分布，得到分位与期望差。

    样本不足以覆盖某个窗口时该窗口返回 None（前端留空），不会用短样本
    外推成年化指标——上线初期的样本量本就不具备确认能力。

    Args:
        daily_returns: 上线后（验证期）组合日收益率序列（%）。
        benchmark_returns: 同区间基准日收益率序列（%），可为 None。
        baseline: ``build_baseline_distribution`` 产出的研究期分布快照。

    Returns:
        含 windows（逐窗口分位）与 drawdown（回撤分位）的字典。
    """
    n = len(daily_returns)
    benchmark_returns = clean_returns_series(benchmark_returns, n) if n else None
    windows_baseline: dict = (baseline or {}).get("windows", {}) or {}
    result: dict = {"sample_days": n, "excess_degradation": {}, "windows": {}}

    for label, days in WINDOW_DAYS.items():
        entry: dict = {"days": days, "available": n >= days}
        base_entry = windows_baseline.get(label)
        if n >= days and base_entry:
            excess = _window_excess(daily_returns, benchmark_returns, days)
            sharpe = _annualized_sharpe(daily_returns[-days:])
            entry["excess_return_pct"] = round(excess, 4)
            entry["excess_return_percentile_pct"] = _percentile_rank(
                base_entry["excess_return_pct"], excess
            )
            entry["sharpe_ratio"] = round(sharpe, 4)
            entry["sharpe_percentile_pct"] = _percentile_rank(
                base_entry["sharpe_ratio"], sharpe
            )
            # 期望差：当前超额 − 研究期同窗口超额中位数（正数表示好于历史中位）
            median = base_entry["excess_return_pct"].get("50")
            entry["expectation_gap_pct"] = (
                round(excess - median, 4) if median is not None else None
            )
        result["windows"][label] = entry

    drawdown_table = (baseline or {}).get("drawdown_pct") or {}
    if n:
        current_dd = abs(_drawdown_series(daily_returns)[-1])
        result["drawdown"] = {
            "current_pct": round(current_dd, 4),
            "max_pct": round(max(abs(d) for d in _drawdown_series(daily_returns)), 4),
            "percentile_pct": _percentile_rank(drawdown_table, current_dd),
        }
    else:
        result["drawdown"] = {"current_pct": 0.0, "max_pct": 0.0, "percentile_pct": None}
    return result


def assess_health(
    drawdown_percentile_pct: float | None,
    window_percentiles: dict[str, float | None],
    trailing_alpha_percentiles: list[float | None] | None = None,
    ic_decay: dict[str, Any] | None = None,
    drawdown_warning_percentile: float = 95.0,
    alpha_warning_percentile: float = 5.0,
    consecutive_windows: int = 2,
) -> HealthAssessment:
    """按策略自身的历史分布判定健康等级与建议动作。

    判定优先级（高到低）：
    1. 回撤极端（分位 > ``drawdown_warning_percentile``）且存在单调衰减
       → CRITICAL / ALPHA_DECAY；
    2. 回撤极端 → WARNING / DRAWDOWN_EXTREME / REDUCE_RISK；
    3. 超额随窗口单调衰减且 IC 同向衰减 → WARNING / ALPHA_DECAY / RESEARCH；
    4. 3M 超额分位连续 ``consecutive_windows`` 次低于 ``alpha_warning_percentile``
       → WATCH / ALPHA_DECAY；
    5. 所有监控窗口样本不足 → UNKNOWN / INSUFFICIENT_DATA / KEEP
       （"没算出问题"不记为"健康"）；
    6. 其余 → HEALTHY / NORMAL / KEEP。

    Args:
        drawdown_percentile_pct: 当前回撤在研究期回撤分布中的分位（0-100）。
        window_percentiles: 各窗口超额收益分位，键为窗口标签（如 "3m"）。
        trailing_alpha_percentiles: 历次快照的 3M 超额分位（最近在前），
            用于判断"连续多次低于阈值"。
        ic_decay: 因子 IC 前后半段的衰减证据，含 ``first_half_mean`` /
            ``second_half_mean``，以及服务层按标准误算出的 ``confirmed``。
            ``confirmed`` 存在时以其为准；不存在时回退为"后半段均值更低"。
            不可用时传 None，此时不做否决。
        drawdown_warning_percentile: 回撤告警分位阈值，默认 95。
        alpha_warning_percentile: 超额告警分位阈值，默认 5。
        consecutive_windows: 连续低于阈值的次数要求，默认 2。

    Returns:
        HealthAssessment 实例。
    """
    reasons: list[str] = []
    performance_decay = _has_monotonic_performance_decay(window_percentiles)
    confirmed_decay = performance_decay and _has_ic_decay(ic_decay)
    drawdown_extreme = (
        drawdown_percentile_pct is not None
        and drawdown_percentile_pct > drawdown_warning_percentile
    )

    if drawdown_extreme and confirmed_decay:
        reasons.append(
            f"当前回撤处于研究期 {drawdown_percentile_pct:.1f} 分位，且超额与 IC 同步衰减"
        )
        return HealthAssessment(
            HEALTH_CRITICAL, DIAGNOSIS_ALPHA_DECAY, ACTION_RESEARCH, reasons
        )
    if drawdown_extreme:
        reasons.append(f"当前回撤处于研究期 {drawdown_percentile_pct:.1f} 分位")
        return HealthAssessment(
            HEALTH_WARNING, DIAGNOSIS_DRAWDOWN_EXTREME, ACTION_REDUCE_RISK, reasons
        )
    if confirmed_decay:
        reasons.append("超额收益随监控窗口单调衰减，且因子 IC 同向衰减")
        return HealthAssessment(
            HEALTH_WARNING, DIAGNOSIS_ALPHA_DECAY, ACTION_RESEARCH, reasons
        )
    if performance_decay:
        reasons.append("超额收益随监控窗口单调衰减，但 IC 衰减证据不足")
        return HealthAssessment(HEALTH_WATCH, DIAGNOSIS_ALPHA_DECAY, ACTION_WATCH, reasons)

    recent = [p for p in (trailing_alpha_percentiles or []) if p is not None]
    short_window = window_percentiles.get("3m")
    if short_window is not None:
        recent = [short_window, *recent]
    if len(recent) >= consecutive_windows and all(
        p < alpha_warning_percentile for p in recent[:consecutive_windows]
    ):
        reasons.append(
            f"近 {consecutive_windows} 次观察的 3M 超额分位均低于 "
            f"{alpha_warning_percentile:.0f}（{', '.join(f'{p:.1f}' for p in recent[:consecutive_windows])}）"
        )
        return HealthAssessment(
            HEALTH_WATCH, DIAGNOSIS_ALPHA_DECAY, ACTION_WATCH, reasons
        )

    if window_percentiles and all(v is None for v in window_percentiles.values()):
        reasons.append("上线样本不足以覆盖任何监控窗口，仅记录数据")
        return HealthAssessment(
            HEALTH_UNKNOWN, DIAGNOSIS_INSUFFICIENT_DATA, ACTION_KEEP, reasons
        )

    reasons.append("表现处于研究期历史分布之内")
    return HealthAssessment(HEALTH_HEALTHY, DIAGNOSIS_NORMAL, ACTION_KEEP, reasons)


def _has_monotonic_performance_decay(window_percentiles: dict[str, float | None]) -> bool:
    """判断是否存在“短窗口 < 中窗口 < 长窗口”的收益分位衰减。"""
    order = ["3m", "6m", "12m"]
    values = [window_percentiles.get(label) for label in order]
    if any(v is None for v in values):
        return False
    p3, p6, p12 = values  # type: ignore[misc]
    return p3 < p6 < p12


def ic_decay_evidence(ic_values: Sequence[float]) -> dict[str, Any]:
    """由单个因子的 IC 序列给出前后半段均值与衰减显著性。

    IC 序列通常噪声主导，仅凭"后半段均值更低"无法区分衰减与噪声，因此用
    两个半段均值差与标准误比较：差值超过 ``IC_DECAY_SIGMA`` 倍标准误才算确认。

    Args:
        ic_values: 按日期升序的 IC 观测值（同一因子）。

    Returns:
        含 first_half_mean / second_half_mean / delta / se / first_half_n /
        second_half_n / confirmed 的字典；半段观测不足时均值为 None 且
        ``confirmed`` 为 False。
    """
    total = len(ic_values)
    result: dict[str, Any] = {
        "first_half_mean": None,
        "second_half_mean": None,
        "delta": None,
        "se": None,
        "first_half_n": total // 2,
        "second_half_n": total - total // 2,
        "confirmed": False,
    }
    if total < IC_DECAY_MIN_HALF_N * 2:
        return result
    middle = total // 2
    first = list(ic_values[:middle])
    second = list(ic_values[middle:])
    if len(first) < IC_DECAY_MIN_HALF_N or len(second) < IC_DECAY_MIN_HALF_N:
        return result

    first_mean = sum(first) / len(first)
    second_mean = sum(second) / len(second)
    first_se = _standard_error(first)
    second_se = _standard_error(second)
    se = math.sqrt(first_se**2 + second_se**2)
    delta = first_mean - second_mean
    result.update(
        {
            "first_half_mean": round(first_mean, 4),
            "second_half_mean": round(second_mean, 4),
            "delta": round(delta, 4),
            "se": round(se, 4),
            "confirmed": bool(se > 0 and delta > IC_DECAY_SIGMA * se),
        }
    )
    return result


def _standard_error(values: Sequence[float]) -> float:
    """计算样本均值的标准误（样本标准差 / sqrt(n)）。"""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(variance / n)


def _has_ic_decay(ic_decay: dict[str, Any] | None) -> bool:
    """判断是否确认因子 IC 衰减。

    ``ic_decay["confirmed"]`` 存在时以其为准（服务层已按标准误做显著性判定）；
    缺失时回退为"后半段均值低于前半段"的旧口径，以兼容外部或历史快照数据。

    Args:
        ic_decay: 含前后半段均值（可选 ``confirmed``）的字典。

    Returns:
        是否确认 IC 衰减。
    """
    if not ic_decay:
        return False
    confirmed = ic_decay.get("confirmed")
    if confirmed is not None:
        return bool(confirmed)
    first = ic_decay.get("first_half_mean")
    second = ic_decay.get("second_half_mean")
    if first is None or second is None:
        return False
    return second < first


def _rolling_excess_windows(
    daily_returns: list[float], benchmark_returns: list[float] | None, window: int
) -> list[float]:
    """计算滚动窗口累计超额收益序列（百分点）。"""
    n = len(daily_returns)
    if n < window:
        return []
    use_benchmark = benchmark_returns is not None
    result: list[float] = []
    for end in range(window, n + 1):
        start = end - window
        excess = _compound(daily_returns[start:end]) - (
            _compound(benchmark_returns[start:end]) if use_benchmark else 0.0
        )
        result.append(excess)
    return result


def _rolling_sharpes(daily_returns: list[float], window: int) -> list[float]:
    """计算滚动窗口年化夏普序列。"""
    n = len(daily_returns)
    if n < window:
        return []
    return [_annualized_sharpe(daily_returns[end - window : end]) for end in range(window, n + 1)]


def _window_excess(
    daily_returns: list[float], benchmark_returns: list[float] | None, window: int
) -> float:
    """计算最近 window 个交易日的累计超额收益（百分点）。"""
    tail = daily_returns[-window:]
    excess = _compound(tail)
    if benchmark_returns is not None and len(benchmark_returns) >= window:
        excess -= _compound(benchmark_returns[-window:])
    return excess


def _compound(daily_returns: list[float]) -> float:
    """计算收益率序列的累计收益（百分点）。"""
    cumulative = 1.0
    for r in daily_returns:
        cumulative *= 1 + r / 100
    return (cumulative - 1) * 100


def _annualized_sharpe(
    daily_returns: list[float], trading_days: int = DEFAULT_TRADING_DAYS_PER_YEAR
) -> float:
    """计算年化夏普（总体标准差口径），样本不足时返回 0。"""
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
    std_r = math.sqrt(variance)
    if std_r <= 0:
        return 0.0
    return mean_r / std_r * math.sqrt(trading_days)


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


def _percentile_table(values: list[float]) -> dict[str, float]:
    """把数值序列压缩成分位点字典（键为分位数字符串）。

    使用线性插值，样本为空时返回空字典；绝对值口径由调用方决定。
    """
    if not values:
        return {}
    ordered = sorted(values)
    table: dict[str, float] = {}
    for point in PERCENTILE_POINTS:
        table[str(point)] = round(_quantile(ordered, point / 100), 4)
    return table


def _quantile(ordered: list[float], q: float) -> float:
    """在已排序序列上取分位数（线性插值）。"""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _percentile_rank(table: dict, value: float) -> float | None:
    """用分位点表反推某个取值所处的分位（0-100，线性插值）。

    Args:
        table: ``_percentile_table`` 产出的分位点字典。
        value: 待定位的取值。

    Returns:
        分位（0-100）；表为空时返回 None。低于最小分位点返回 0，高于最大返回 100。
    """
    if not table:
        return None
    points = sorted((float(k), float(v)) for k, v in table.items())
    if value <= points[0][1]:
        return 0.0 if value < points[0][1] else points[0][0]
    if value >= points[-1][1]:
        return 100.0 if value > points[-1][1] else points[-1][0]
    for (p_low, v_low), (p_high, v_high) in zip(points, points[1:]):
        if v_low <= value <= v_high:
            if v_high == v_low:
                return p_low
            weight = (value - v_low) / (v_high - v_low)
            return round(p_low + weight * (p_high - p_low), 2)
    return None
