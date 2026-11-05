"""回测独立对账（纯领域逻辑，刻意不复用回测执行器的任何代码）。

只用落库的逐日结果（执行后持仓、组合收益、换手、累计收益、回撤、基准收益）与原始
行情，按 ``drift_v1`` 记账口径的**规格**从头复算一遍，再与回测主循环的结果逐日比对。

本模块不导入 ``quant_etf_api`` 下的任何模块（由 ``test_backtest_reconcile.py`` 守卫），
因此主循环里的实现缺陷——日期错位、用错持仓、调仓日漏算一段、漂移或前向填充写错、
累积器或汇总指标算错——都会表现为差异。两边共享同一份口径规格，规格本身的错误
需要外部交叉验证才能发现。

规格（与 AGENTS.md「回测记账口径」一致）：

1. 逐日行 k 的收益窗口为 [收盘 T_k, 收盘 T_{k+1}]，最后一个交易日不落行；
2. 收益行情前向填充：收盘价无效（None、0、NaN）时沿用此前（含回望窗口）最后一个
   有效收盘价，开盘价同取该值；收盘价有效而开盘价无效时开盘价取收盘价；从未出现过
   有效收盘价的资产视为缺失，收益记 0；
3. 持仓在窗口内随行情漂移：``w' = w × (1 + r) / (1 + R)``；
4. T+1 收盘执行：行 k 的执行后持仓承担整个窗口；换手 = ``Σ|w_k − 漂移(w_{k−1})| / 2``；
5. T+1 开盘执行：非调仓日同上且换手为 0；调仓日由旧仓位（上一窗口漂移后）承担隔夜段
   ``收盘 T → 开盘 T+1``，新仓位承担日内段 ``开盘 T+1 → 收盘 T+1``，两段复利；换手按
   "隔夜漂移后的开盘持仓 → 新仓位"计算；
6. 基准：``Σ 权重 × 基准指数窗口收益``（同样使用前向填充行情）；
7. 累计收益与回撤按逐日收益复利；汇总指标按年化系数 252、样本标准差（ddof=1）计算。

落库值有取整（收益 4 位小数、权重 4 位小数、汇总指标 2 或 4 位小数），比对容差见
:class:`ReconcileTolerance`。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

EXECUTION_OPEN = "t_plus_1_open"
EXECUTION_CLOSE = "t_plus_1_close"

# 年化系数与回测汇总指标约定一致
_TRADING_DAYS_PER_YEAR = 252
# 报告中保留的差异明细上限（总数另计），避免严重错位时输出上万行
_MAX_ISSUES = 50


class DailyRowLike(Protocol):
    """对账所需的逐日结果字段（落库的 ``BacktestDailyResultModel`` 满足该协议）。"""

    trade_date: date
    portfolio_return: float
    cumulative_return: float
    drawdown: float
    executed_positions: dict[str, float] | None
    turnover: float | None
    benchmark_return: float | None


@dataclass(frozen=True)
class ReconcileTolerance:
    """对账容差（收益类单位为百分点，权重 / 换手为小数）。

    Attributes:
        return_pp: 单日组合收益与基准收益允许差的固定部分（收益本身取整到 4 位小数）。
        return_per_asset_move: 单日组合收益允许差中随行情幅度缩放的部分：落库权重取整到
            4 位小数，每个持仓资产贡献至多约 ``5e-5 × |资产收益|`` 的误差，大涨大跌日
            （如 2024-09 单日 +10%）需要相应放宽。
        weight: 判定"持仓是否发生变化"的单资产权重容差（两侧各有 4 位小数取整）。
        turnover_per_asset: 换手允许差中按涉及资产数累加的部分。
        accumulator_pp: 由落库日收益复利得到的累计收益 / 回撤与落库值的允许差。
        period_pp: 全期复算收益与落库累计收益的允许差（逐日取整误差近似零均值，
            全期累积后仍应很小；系统性偏差会在这里暴露）。
        metric_2dp: 2 位小数汇总指标（累计 / 年化收益、最大回撤、基准收益）的允许差。
        metric_4dp: 4 位小数汇总指标（夏普）的允许差。
    """

    return_pp: float = 1e-3
    return_per_asset_move: float = 6e-5
    weight: float = 2e-4
    turnover_per_asset: float = 1.5e-4
    accumulator_pp: float = 5e-4
    period_pp: float = 0.05
    metric_2dp: float = 0.011
    metric_4dp: float = 2e-4


@dataclass(frozen=True)
class ReconcileIssue:
    """一条对账差异。

    Attributes:
        field: 差异字段（如 ``portfolio_return``、``turnover``、``metrics.sharpe_ratio``）。
        trade_date: 所在逐日行的交易日；全期 / 汇总指标差异为 None。
        expected: 独立复算值。
        actual: 落库值。
        message: 中文说明。
    """

    field: str
    trade_date: date | None
    expected: float | None
    actual: float | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        """转为 JSON 友好的字典。"""
        return {
            "field": self.field,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }


@dataclass
class ReconcileReport:
    """对账报告。

    Attributes:
        execution_model: 执行模型。
        checked_days: 已核对的逐日行数。
        issue_count: 差异总数（``issues`` 只保留前若干条明细）。
        issues: 差异明细。
        max_diffs: 各字段的最大绝对差（含未超出容差的部分），用于观察取整噪声水平。
        period_expected_pct: 由独立复算的逐日收益复利得到的全期收益（%）。
        period_stored_pct: 由落库逐日收益复利得到的全期收益（%）。
        skipped: 未核对项及原因。
    """

    execution_model: str
    checked_days: int = 0
    issue_count: int = 0
    issues: list[ReconcileIssue] = field(default_factory=list)
    max_diffs: dict[str, float] = field(default_factory=dict)
    period_expected_pct: float = 0.0
    period_stored_pct: float = 0.0
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """没有任何超出容差的差异。"""
        return self.issue_count == 0

    def add(self, issue: ReconcileIssue) -> None:
        """登记一条差异（明细超过上限后只计数）。"""
        self.issue_count += 1
        if len(self.issues) < _MAX_ISSUES:
            self.issues.append(issue)

    def observe(self, name: str, diff: float) -> None:
        """记录字段的绝对差，保留最大值。"""
        self.max_diffs[name] = max(self.max_diffs.get(name, 0.0), abs(diff))

    def to_dict(self) -> dict[str, Any]:
        """转为 JSON 友好的字典。"""
        return {
            "ok": self.ok,
            "execution_model": self.execution_model,
            "checked_days": self.checked_days,
            "issue_count": self.issue_count,
            "issues": [issue.to_dict() for issue in self.issues],
            "max_diffs": {key: round(value, 8) for key, value in sorted(self.max_diffs.items())},
            "period_expected_pct": round(self.period_expected_pct, 6),
            "period_stored_pct": round(self.period_stored_pct, 6),
            "skipped": list(self.skipped),
        }


@dataclass(frozen=True)
class _Price:
    """前向填充后的单日价格（None 表示该资产此前从未有过有效收盘价）。"""

    open: float | None
    close: float | None


def _valid(value: Any) -> bool:
    """价格是否可用于收益计算（None、0、NaN 视为无效）。"""
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number != 0 and not math.isnan(number)


def build_price_table(
    bars: Mapping[tuple[str, date], Any],
    codes: Sequence[str],
    trading_dates: Sequence[date],
) -> dict[str, list[_Price]]:
    """按规格 2 构造前向填充价格表。

    Args:
        bars: 原始行情 ``(代码, 日期) → 行情``，需含回望窗口（用于首日缺口的前值）。
        codes: 需要的指数代码。
        trading_dates: 回测交易日（升序，含最后一个交易日）。

    Returns:
        ``代码 → 与交易日等长的价格列表``。
    """
    wanted = set(codes)
    by_code: dict[str, dict[date, Any]] = {code: {} for code in wanted}
    for (code, day), bar in bars.items():
        if code in wanted:
            by_code[code][day] = bar
    first = trading_dates[0] if trading_dates else None
    table: dict[str, list[_Price]] = {}
    for code in sorted(wanted):
        series = by_code[code]
        last_close: float | None = None
        if first is not None:
            earlier = [
                day
                for day, bar in series.items()
                if day < first and _valid(getattr(bar, "close_price", None))
            ]
            if earlier:
                last_close = float(series[max(earlier)].close_price)
        prices: list[_Price] = []
        for day in trading_dates:
            bar = series.get(day)
            close = getattr(bar, "close_price", None) if bar is not None else None
            if _valid(close):
                last_close = float(close)
                raw_open = getattr(bar, "open_price", None)
                prices.append(
                    _Price(
                        open=float(raw_open) if _valid(raw_open) else last_close, close=last_close
                    )
                )
            else:
                prices.append(_Price(open=last_close, close=last_close))
        table[code] = prices
    return table


def _pct(end: float | None, start: float | None) -> float | None:
    """区间收益率（%）；任一端缺失时为 None。"""
    if end is None or start is None or start == 0:
        return None
    return (end / start - 1) * 100


def _weighted(weights: Mapping[str, float], returns: Mapping[str, float | None]) -> float:
    """组合收益（%）：缺失收益按 0 计。"""
    return sum(weight * (returns.get(code) or 0.0) for code, weight in sorted(weights.items()))


def _drift(weights: Mapping[str, float], returns: Mapping[str, float | None]) -> dict[str, float]:
    """按规格 3 把窗口起点权重推进到窗口终点。"""
    growth = 1 + _weighted(weights, returns) / 100
    if growth <= 0:
        return dict(weights)
    drifted = {
        code: weight * (1 + (returns.get(code) or 0.0) / 100) / growth
        for code, weight in weights.items()
    }
    return {code: value for code, value in drifted.items() if value > 1e-12}


def _half_l1(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """单边换手 Σ|a − b| / 2。"""
    return sum(abs(a.get(code, 0.0) - b.get(code, 0.0)) for code in set(a) | set(b)) / 2


def _max_abs_diff(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """两组权重的最大单资产绝对差。"""
    return max((abs(a.get(code, 0.0) - b.get(code, 0.0)) for code in set(a) | set(b)), default=0.0)


def reconcile_daily(
    rows: Sequence[DailyRowLike],
    trading_dates: Sequence[date],
    bars: Mapping[tuple[str, date], Any],
    *,
    execution_model: str,
    benchmark_mix: Sequence[tuple[str, float]] = (),
    tolerance: ReconcileTolerance | None = None,
) -> ReconcileReport:
    """逐日复算组合收益、换手、累计收益、回撤与基准收益，并与落库值比对。

    Args:
        rows: 落库逐日结果（按交易日升序，每个决策日一行）。
        trading_dates: 回测交易日（升序）；必须以逐日行的日期开头，且多出最后一个交易日。
        bars: 原始行情（含回望窗口）。
        execution_model: ``t_plus_1_open`` 或 ``t_plus_1_close``。
        benchmark_mix: 基准构成 ``[(指数代码, 权重)]``；为空表示不核对基准。
        tolerance: 容差，缺省用 :class:`ReconcileTolerance` 默认值。

    Returns:
        对账报告。

    Raises:
        ValueError: 执行模型未知，或交易日序列与逐日行不对齐。
    """
    if execution_model not in (EXECUTION_OPEN, EXECUTION_CLOSE):
        raise ValueError(f"未知的执行模型: {execution_model}")
    tol = tolerance or ReconcileTolerance()
    report = ReconcileReport(execution_model=execution_model)
    if not rows:
        report.skipped.append("没有逐日结果")
        return report
    row_dates = [row.trade_date for row in rows]
    if len(trading_dates) != len(rows) + 1 or list(trading_dates[: len(rows)]) != row_dates:
        raise ValueError("交易日序列必须等于逐日行日期再加上最后一个交易日")

    codes = {code for row in rows for code in (row.executed_positions or {})}
    codes.update(code for code, _weight in benchmark_mix)
    prices = build_price_table(bars, sorted(codes), trading_dates)

    def window(code: str, k: int, leg: str) -> float | None:
        """资产在第 k 个窗口某一段的收益（%）：full / overnight / intraday。"""
        series = prices.get(code)
        if series is None:
            return None
        start, end = series[k], series[k + 1]
        if leg == "overnight":
            return _pct(end.open, start.close)
        if leg == "intraday":
            return _pct(end.close, end.open)
        return _pct(end.close, start.close)

    def leg_returns(weights: Mapping[str, float], k: int, leg: str) -> dict[str, float | None]:
        """一组持仓在第 k 个窗口某一段的逐资产收益。"""
        return {code: window(code, k, leg) for code in weights}

    previous_end: dict[str, float] = {}
    expected_growth = 1.0
    stored_growth = 1.0
    stored_peak = 1.0
    for k, row in enumerate(rows):
        weights = {code: float(w) for code, w in (row.executed_positions or {}).items() if w}
        stored_turnover = float(row.turnover or 0.0)
        turnover_tol = tol.turnover_per_asset * max(1, len(set(weights) | set(previous_end)))
        traded = stored_turnover > 0 or _max_abs_diff(weights, previous_end) > tol.weight

        if execution_model == EXECUTION_OPEN and traded:
            overnight = leg_returns(previous_end, k, "overnight")
            weights_at_open = _drift(previous_end, overnight)
            intraday = leg_returns(weights, k, "intraday")
            expected_return = (
                (1 + _weighted(previous_end, overnight) / 100)
                * (1 + _weighted(weights, intraday) / 100)
                - 1
            ) * 100
            expected_turnover = _half_l1(weights, weights_at_open)
            end_weights = _drift(weights, intraday)
            moves = [*overnight.values(), *intraday.values()]
        else:
            full = leg_returns(weights, k, "full")
            expected_return = _weighted(weights, full)
            expected_turnover = _half_l1(weights, previous_end)
            end_weights = _drift(weights, full)
            moves = list(full.values())

        day = row.trade_date
        return_tol = tol.return_pp + tol.return_per_asset_move * sum(abs(m or 0.0) for m in moves)
        _check(report, "portfolio_return", day, expected_return, row.portfolio_return, return_tol)
        if stored_turnover > 0:
            _check(report, "turnover", day, expected_turnover, stored_turnover, turnover_tol)
        else:
            report.observe("turnover", expected_turnover)
            if expected_turnover > turnover_tol:
                report.add(
                    ReconcileIssue(
                        field="continuity",
                        trade_date=day,
                        expected=round(expected_turnover, 6),
                        actual=0.0,
                        message="未记换手，但持仓与上一窗口漂移后的持仓不一致",
                    )
                )

        if benchmark_mix:
            expected_bench = sum(
                weight * (window(code, k, "full") or 0.0) for code, weight in benchmark_mix
            )
            if row.benchmark_return is None:
                report.add(
                    ReconcileIssue(
                        field="benchmark_return",
                        trade_date=day,
                        expected=round(expected_bench, 6),
                        actual=None,
                        message="启用了基准但逐日行缺少基准收益",
                    )
                )
            else:
                _check(
                    report,
                    "benchmark_return",
                    day,
                    expected_bench,
                    row.benchmark_return,
                    tol.return_pp,
                )

        # 累积器：只用落库日收益复利，核对累计收益与回撤的落账
        stored_growth *= 1 + float(row.portfolio_return) / 100
        stored_peak = max(stored_peak, stored_growth)
        _check(
            report,
            "cumulative_return",
            day,
            (stored_growth - 1) * 100,
            row.cumulative_return,
            tol.accumulator_pp,
        )
        _check(
            report,
            "drawdown",
            day,
            (stored_growth / stored_peak - 1) * 100,
            row.drawdown,
            tol.accumulator_pp,
        )

        expected_growth *= 1 + expected_return / 100
        previous_end = end_weights
        report.checked_days += 1

    report.period_expected_pct = (expected_growth - 1) * 100
    report.period_stored_pct = (stored_growth - 1) * 100
    _check(
        report,
        "period_return",
        None,
        report.period_expected_pct,
        report.period_stored_pct,
        tol.period_pp,
    )
    return report


def reconcile_metrics(
    rows: Sequence[DailyRowLike],
    metrics: Mapping[str, Any],
    report: ReconcileReport,
    *,
    annual_risk_free_rate_pct: float = 0.0,
    tolerance: ReconcileTolerance | None = None,
) -> None:
    """由落库逐日收益独立复算汇总指标，差异写入报告。

    Args:
        rows: 落库逐日结果。
        metrics: 落库汇总指标（``backtest_run.metrics``）。
        report: 写入差异的对账报告。
        annual_risk_free_rate_pct: 回测使用的年化无风险利率（%）；非 0 时夏普不核对。
        tolerance: 容差。
    """
    tol = tolerance or ReconcileTolerance()
    returns = [float(row.portfolio_return) for row in rows]
    n = len(returns)
    if n == 0:
        return
    growth = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        growth *= 1 + value / 100
        peak = max(peak, growth)
        worst = min(worst, (growth / peak - 1) * 100)
    total = (growth - 1) * 100
    annualized = -100.0 if growth <= 0 else (growth ** (_TRADING_DAYS_PER_YEAR / n) - 1) * 100
    # 持仓日：执行后持仓里至少有一个正权重（空仓日不进入胜率分母）
    held = [float(row.portfolio_return) for row in rows if _has_weight(row.executed_positions)]
    win_base = held or returns
    expected: dict[str, float] = {
        "total_trading_days": float(n),
        "active_days": float(len(held)),
        "cumulative_return_pct": total,
        "annualized_return_pct": annualized,
        "max_drawdown_pct": worst,
        "win_rate_pct": sum(1 for value in win_base if value > 0) / len(win_base) * 100,
    }
    if annual_risk_free_rate_pct == 0 and n >= 2:
        mean = sum(returns) / n
        std = math.sqrt(sum((value - mean) ** 2 for value in returns) / (n - 1))
        expected["sharpe_ratio"] = mean / std * math.sqrt(_TRADING_DAYS_PER_YEAR) if std else 0.0
    else:
        report.skipped.append("夏普：回测使用了非零无风险利率，未核对")
    bench = [row.benchmark_return for row in rows]
    if metrics.get("benchmark_return_pct") is not None and all(b is not None for b in bench):
        bench_growth = 1.0
        for value in bench:
            bench_growth *= 1 + float(value or 0.0) / 100
        expected["benchmark_return_pct"] = (bench_growth - 1) * 100

    tolerances = {
        "total_trading_days": 0.0,
        "active_days": 0.0,
        "sharpe_ratio": tol.metric_4dp,
    }
    for key, value in expected.items():
        stored = metrics.get(key)
        if stored is None:
            report.skipped.append(f"汇总指标 {key}：落库值为空")
            continue
        _check(
            report,
            f"metrics.{key}",
            None,
            value,
            float(stored),
            tolerances.get(key, tol.metric_2dp),
        )


def _has_weight(positions: Mapping[str, float] | None) -> bool:
    """持仓里是否至少有一个正权重。"""
    return any(float(weight) > 0 for weight in (positions or {}).values())


def _check(
    report: ReconcileReport,
    name: str,
    trade_date: date | None,
    expected: float,
    actual: float | None,
    limit: float,
) -> None:
    """比对一个数值，记录最大差并在超出容差时登记差异。"""
    if actual is None:
        report.add(
            ReconcileIssue(
                field=name,
                trade_date=trade_date,
                expected=round(expected, 6),
                actual=None,
                message="落库值缺失",
            )
        )
        return
    diff = float(actual) - expected
    report.observe(name, diff)
    if abs(diff) > limit + 1e-12:
        report.add(
            ReconcileIssue(
                field=name,
                trade_date=trade_date,
                expected=round(expected, 6),
                actual=round(float(actual), 6),
                message=f"差 {diff:+.6f}，超出容差 {limit:g}",
            )
        )
