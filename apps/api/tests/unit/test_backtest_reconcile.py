"""回测独立对账（``domain/portfolio/reconcile.py``）的测试。

覆盖：
1. 独立性：对账模块不导入 ``quant_etf_api`` 下任何模块（不能复用执行器代码）；
2. 手算样例：T+1 收盘执行的建仓 / 漂移、T+1 开盘执行的调仓日两段复利、收益行情
   前向填充，以及对应的错误口径（每日免费再平衡、两段直接相加、缺口涨跌丢失）
   都能被识别；
3. 真实主循环：金样本三条策略的落库结果与独立复算一致；
4. 注入错误：篡改单日收益、篡改持仓、行情错位一天、篡改汇总指标都会报出差异；
5. 服务层（``backtest verify``）：按回测 params 解析口径，拒绝不支持的回测，
   行情被修订时给出区分来源的提示。
"""

from __future__ import annotations

import ast
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from quant_etf_api.domain.portfolio import reconcile as reconcile_module
from quant_etf_api.domain.portfolio.reconcile import (
    ReconcileReport,
    reconcile_daily,
    reconcile_metrics,
)
from quant_etf_api.services.backtest_reconcile_service import BacktestReconcileService
from tests.unit.test_caliber_golden import cases, run_outcome

D0, D1, D2, D3 = date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)


def _bar(close: float | None, open_price: float | None = None) -> SimpleNamespace:
    """构造行情（开盘价缺省等于收盘价）。"""
    return SimpleNamespace(
        close_price=close, open_price=close if open_price is None else open_price
    )


def _row(
    day: date,
    ret: float,
    positions: dict[str, float] | None,
    turnover: float | None = None,
    benchmark: float | None = None,
    *,
    cum: float = 0.0,
    dd: float = 0.0,
) -> SimpleNamespace:
    """构造逐日结果行。"""
    return SimpleNamespace(
        trade_date=day,
        portfolio_return=ret,
        cumulative_return=cum,
        drawdown=dd,
        executed_positions=positions,
        turnover=turnover,
        benchmark_return=benchmark,
    )


def _with_accumulator(rows: list[SimpleNamespace]) -> list[SimpleNamespace]:
    """按落库日收益补齐累计收益与回撤（手算样例只关心收益与持仓）。"""
    growth, peak = 1.0, 1.0
    for row in rows:
        growth *= 1 + row.portfolio_return / 100
        peak = max(peak, growth)
        row.cumulative_return = round((growth - 1) * 100, 4)
        row.drawdown = round((growth / peak - 1) * 100, 4)
    return rows


def _fields(report: ReconcileReport) -> set[str]:
    """报告中出现差异的字段集合。"""
    return {issue.field for issue in report.issues}


class TestIndependence:
    """对账模块必须是独立实现。"""

    def test_imports_nothing_from_the_package(self) -> None:
        """不导入 quant_etf_api 下任何模块：复用执行器代码会让两边一起错、一起"对上"。"""
        source = Path(reconcile_module.__file__).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, "对账模块不得使用相对导入"
                imported.add(node.module or "")
        assert not [name for name in imported if name.split(".")[0] == "quant_etf_api"]


class TestCloseModelHandComputed:
    """T+1 收盘执行：D0 决策、D1 收盘建仓、D2 持有漂移。"""

    _bars = {
        ("a", D0): _bar(100.0),
        ("a", D1): _bar(110.0),
        ("a", D2): _bar(99.0),
        ("a", D3): _bar(99.0),
        ("b", D0): _bar(100.0),
        ("b", D1): _bar(100.0),
        ("b", D2): _bar(100.0),
        ("b", D3): _bar(110.0),
    }

    def _rows(self, hold_day: dict[str, float], hold_ret: float) -> list[SimpleNamespace]:
        """D0 空仓；D1 收盘建仓 a/b 各半（a 跌 10%）；D2 持有（b 涨 10%）。"""
        return _with_accumulator(
            [
                _row(D0, 0.0, None),
                _row(D1, -5.0, {"a": 0.5, "b": 0.5}, turnover=0.5),
                _row(D2, hold_ret, hold_day),
            ]
        )

    def test_drifted_holdings_reconcile(self) -> None:
        """漂移后 a=0.5×0.9/0.95、b=0.5/0.95，D2 收益 = 0.5263×10%。"""
        rows = self._rows({"a": 0.4737, "b": 0.5263}, 5.2632)
        report = reconcile_daily(
            rows, [D0, D1, D2, D3], self._bars, execution_model="t_plus_1_close"
        )
        assert report.ok, [i.to_dict() for i in report.issues]
        assert report.checked_days == 3
        assert report.period_expected_pct == pytest.approx(0.0, abs=1e-3)

    def test_free_daily_rebalance_is_flagged(self) -> None:
        """旧口径：非调仓日被免费调回目标权重——持仓不连续却未记换手。"""
        rows = self._rows({"a": 0.5, "b": 0.5}, 5.0)
        report = reconcile_daily(
            rows, [D0, D1, D2, D3], self._bars, execution_model="t_plus_1_close"
        )
        assert "continuity" in _fields(report)

    def test_wrong_turnover_is_flagged(self) -> None:
        """建仓换手应为 0.5（从现金买入 Σ|w|/2），记成 1.0 报差异。"""
        rows = self._rows({"a": 0.4737, "b": 0.5263}, 5.2632)
        rows[1].turnover = 1.0
        report = reconcile_daily(
            rows, [D0, D1, D2, D3], self._bars, execution_model="t_plus_1_close"
        )
        assert _fields(report) == {"turnover"}

    def test_misaligned_trading_dates_rejected(self) -> None:
        """交易日序列必须是逐日行日期再加一个终止日。"""
        rows = self._rows({"a": 0.4737, "b": 0.5263}, 5.2632)
        with pytest.raises(ValueError, match="交易日序列"):
            reconcile_daily(rows, [D0, D1, D2], self._bars, execution_model="t_plus_1_close")


class TestOpenModelHandComputed:
    """T+1 开盘执行：D0 决策建仓 a，D1 决策换到 b。"""

    _bars = {
        ("a", D0): _bar(100.0),
        ("a", D1): _bar(105.0, 102.0),
        ("a", D2): _bar(104.0, 104.0),
        ("b", D0): _bar(100.0),
        ("b", D1): _bar(101.0, 100.0),
        ("b", D2): _bar(103.0, 101.0),
    }

    def _rows(self, switch_ret: float) -> list[SimpleNamespace]:
        """D0：现金 → a，只有日内段 105/102；D1：a → b，隔夜 104/105 复利日内 103/101。"""
        return _with_accumulator(
            [
                _row(D0, 2.9412, {"a": 1.0}, turnover=0.5),
                _row(D1, switch_ret, {"b": 1.0}, turnover=1.0),
            ]
        )

    def test_two_legs_compound(self) -> None:
        """(1 − 0.9524%)(1 + 1.9802%) − 1 = 1.0090%。"""
        report = reconcile_daily(
            self._rows(1.009), [D0, D1, D2], self._bars, execution_model="t_plus_1_open"
        )
        assert report.ok, [i.to_dict() for i in report.issues]

    def test_legs_added_instead_of_compounded_is_flagged(self) -> None:
        """旧口径两段直接相加：−0.9524% + 1.9802% = 1.0278%，差 0.019 个百分点。"""
        report = reconcile_daily(
            self._rows(1.0278), [D0, D1, D2], self._bars, execution_model="t_plus_1_open"
        )
        assert "portfolio_return" in _fields(report)

    def test_close_model_assumption_is_flagged(self) -> None:
        """同一组落库结果按 T+1 收盘口径核对必然对不上（执行模型错配）。"""
        report = reconcile_daily(
            self._rows(1.009), [D0, D1, D2], self._bars, execution_model="t_plus_1_close"
        )
        assert not report.ok


class TestForwardFill:
    """收益行情前向填充：缺口期间平盘、缺口后补回涨跌。"""

    _bars = {
        ("a", date(2024, 12, 31)): _bar(100.0),
        ("a", D0): _bar(100.0),
        ("a", D1): _bar(None),
        ("a", D2): _bar(110.0),
    }

    def test_gap_move_recovered(self) -> None:
        """D1 缺收盘价：D0 窗口收益 0，D1 窗口相对 100 结算 +10%。"""
        rows = _with_accumulator(
            [_row(D0, 0.0, {"a": 1.0}, turnover=0.5), _row(D1, 10.0, {"a": 1.0})]
        )
        report = reconcile_daily(rows, [D0, D1, D2], self._bars, execution_model="t_plus_1_close")
        assert report.ok, [i.to_dict() for i in report.issues]

    def test_lost_gap_move_is_flagged(self) -> None:
        """缺口后的涨跌被永久丢失（旧口径）会报收益差异。"""
        rows = _with_accumulator(
            [_row(D0, 0.0, {"a": 1.0}, turnover=0.5), _row(D1, 0.0, {"a": 1.0})]
        )
        report = reconcile_daily(rows, [D0, D1, D2], self._bars, execution_model="t_plus_1_close")
        assert {"portfolio_return", "period_return"} <= _fields(report)

    def test_seed_comes_from_lookback(self) -> None:
        """首个交易日就缺行情时，用回望窗口里的最后一个有效收盘价作前值。"""
        bars = {
            ("a", date(2024, 12, 31)): _bar(100.0),
            ("a", D0): _bar(None),
            ("a", D1): _bar(105.0),
        }
        rows = _with_accumulator([_row(D0, 5.0, {"a": 1.0}, turnover=0.5)])
        report = reconcile_daily(rows, [D0, D1], bars, execution_model="t_plus_1_close")
        assert report.ok, [i.to_dict() for i in report.issues]


# ── 真实主循环 ──────────────────────────────────────────────────────────


def _loop(case: str) -> tuple[dict[str, Any], dict[tuple[str, date], Any], str]:
    """跑一条金样本用例，返回 (主循环结果, 行情, 执行模型)。"""
    logging.disable(logging.CRITICAL)
    try:
        config, model = cases()[case]
        outcome, bars = run_outcome(config, model)
    finally:
        logging.disable(logging.NOTSET)
    return outcome, bars, model


def _reconcile(
    outcome: dict[str, Any],
    bars: dict[tuple[str, date], Any],
    model: str,
    rows: list[Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> ReconcileReport:
    """对主循环结果做逐日 + 汇总指标对账（金样本基准指数为 000300）。"""
    daily = rows if rows is not None else outcome["daily_rows"]
    report = reconcile_daily(
        daily,
        outcome["trading_dates"],
        bars,
        execution_model=model,
        benchmark_mix=[("000300", 1.0)],
    )
    reconcile_metrics(daily, metrics if metrics is not None else outcome["metrics"], report)
    return report


def _copy_rows(outcome: dict[str, Any]) -> list[SimpleNamespace]:
    """把落库行复制成可修改的对象。"""
    return [
        SimpleNamespace(
            trade_date=r.trade_date,
            portfolio_return=r.portfolio_return,
            cumulative_return=r.cumulative_return,
            drawdown=r.drawdown,
            executed_positions=dict(r.executed_positions or {}),
            turnover=r.turnover,
            benchmark_return=r.benchmark_return,
        )
        for r in outcome["daily_rows"]
    ]


class TestRealLoop:
    """真实回测主循环的输出与独立复算一致。"""

    @pytest.mark.parametrize(
        "case", ["rotation_weekly_close", "risk_parity_overlay_close", "timing_weighted_open"]
    )
    def test_golden_cases_reconcile(self, case: str) -> None:
        """逐日收益、换手、累计收益、回撤、基准与汇总指标全部在容差内。

        择时用例含 26 个降到零仓位的空仓日：2026-11-05 之前主循环把零权重持仓算作
        持仓日，``active_days`` 与胜率在这里对不上（见决策记录）。
        """
        outcome, bars, model = _loop(case)
        report = _reconcile(outcome, bars, model)
        assert report.ok, [issue.to_dict() for issue in report.issues]
        assert report.checked_days == len(outcome["daily_rows"])


class TestInjectedErrors:
    """对真实主循环的结果注入错误，对账必须报出来。"""

    @pytest.fixture(scope="class")
    def rotation(self) -> tuple[dict[str, Any], dict[tuple[str, date], Any], str]:
        """轮动用例（T+1 收盘、周度调仓、含行情缺口）。"""
        return _loop("rotation_weekly_close")

    def test_single_day_return_error(self, rotation: Any) -> None:
        """单日收益差 0.01 个百分点即被定位到该交易日。"""
        outcome, bars, model = rotation
        rows = _copy_rows(outcome)
        rows[40].portfolio_return += 0.01
        report = _reconcile(outcome, bars, model, rows=rows)
        dates = {issue.trade_date for issue in report.issues if issue.field == "portfolio_return"}
        assert dates == {rows[40].trade_date}

    def test_holdings_tampered_on_hold_day(self, rotation: Any) -> None:
        """非调仓日持仓被改动（如漏记一次调仓）报持仓不连续。"""
        outcome, bars, model = rotation
        rows = _copy_rows(outcome)
        k = next(
            i
            for i in range(30, len(rows))
            if not rows[i].turnover and len(rows[i].executed_positions) == 2
        )
        first, second = sorted(rows[k].executed_positions)
        rows[k].executed_positions[first] += 0.05
        rows[k].executed_positions[second] -= 0.05
        report = _reconcile(outcome, bars, model, rows=rows)
        assert "continuity" in _fields(report)

    def test_price_series_shifted_one_day(self, rotation: Any) -> None:
        """行情整体错位一个交易日（典型的日期对齐缺陷）造成大面积收益差异。"""
        outcome, bars, model = rotation
        days = sorted({day for _code, day in bars})
        shifted = {
            (code, days[days.index(day) + 1]): bar
            for (code, day), bar in bars.items()
            if days.index(day) + 1 < len(days)
        }
        report = _reconcile(outcome, shifted, model)
        assert report.issue_count > len(outcome["daily_rows"]) // 2

    def test_metric_tampered(self, rotation: Any) -> None:
        """汇总夏普被改动 0.01 报差异。"""
        outcome, bars, model = rotation
        metrics = dict(outcome["metrics"])
        metrics["sharpe_ratio"] = metrics["sharpe_ratio"] + 0.01
        report = _reconcile(outcome, bars, model, metrics=metrics)
        assert _fields(report) == {"metrics.sharpe_ratio"}


# ── 服务层（backtest verify）──────────────────────────────────────────────


def _service(
    outcome: dict[str, Any],
    bars: dict[tuple[str, date], Any],
    *,
    status: str = "success",
    params: dict[str, Any] | None = None,
    has_next_day: bool = True,
    metrics: dict[str, Any] | None = None,
) -> BacktestReconcileService:
    """用打桩仓库把主循环结果当作"落库回测"交给对账服务。"""
    dates = outcome["trading_dates"]
    run = SimpleNamespace(
        backtest_id="bt-verify",
        strategy_id="golden",
        status=status,
        params=params if params is not None else outcome["params"],
        start_date=dates[0],
        end_date=dates[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300"]},
        metrics=metrics if metrics is not None else outcome["metrics"],
    )
    backtest_repo = MagicMock()
    backtest_repo.find_by_id.side_effect = lambda bid: run if bid == "bt-verify" else None
    backtest_repo.find_daily_results.return_value = outcome["daily_rows"]
    bar_repo = MagicMock()
    bar_repo.find_trading_dates.side_effect = lambda start, end, codes: (
        [d for d in dates if start <= d <= end] if has_next_day else []
    )
    bar_repo.find_all_date_range.side_effect = lambda start, end, index_codes: {
        key: bar for key, bar in bars.items() if start <= key[1] <= end and key[0] in index_codes
    }
    return BacktestReconcileService(
        MagicMock(), backtest_repo=backtest_repo, index_bar_repo=bar_repo
    )


class TestReconcileService:
    """``backtest verify`` 背后的只读对账服务。"""

    @pytest.fixture(scope="class")
    def rotation(self) -> tuple[dict[str, Any], dict[tuple[str, date], Any], str]:
        """轮动用例。"""
        return _loop("rotation_weekly_close")

    def test_verifies_persisted_backtest(self, rotation: Any) -> None:
        """按回测 params 解析执行模型与基准，全部核对通过。"""
        outcome, bars, _model = rotation
        payload = _service(outcome, bars).verify("bt-verify")
        assert payload["ok"], payload["issues"]
        assert payload["execution_model"] == "t_plus_1_close"
        assert payload["benchmark_mix"] == [["000300", 1.0]]
        assert payload["checked_days"] == len(outcome["daily_rows"])
        assert "hint" not in payload

    def test_reports_hint_on_mismatch(self, rotation: Any) -> None:
        """库里行情与回测时不同（模拟上游修订）：报差异并提示如何区分来源。"""
        outcome, bars, _model = rotation
        revised = dict(bars)
        key = next(
            k for k in sorted(bars) if k[0] == "000905" and k[1] > outcome["trading_dates"][60]
        )
        revised[key] = SimpleNamespace(
            close_price=bars[key].close_price * 1.05, open_price=bars[key].open_price
        )
        payload = _service(outcome, revised).verify("bt-verify")
        assert not payload["ok"]
        assert "重跑回测" in payload["hint"]

    def test_hint_for_pre_fix_active_day_defect(self) -> None:
        """修复前的择时回测：只有 active_days / 胜率不符时，提示是已修复的持仓日缺陷。"""
        outcome, bars, _model = _loop("timing_weighted_open")
        # 修复前的落库值：空仓日被算作持仓日（见决策记录 2026-11-05）
        legacy = {**outcome["metrics"], "active_days": 119, "win_rate_pct": 47.06}
        payload = _service(outcome, bars, metrics=legacy).verify("bt-verify")
        assert {issue["field"] for issue in payload["issues"]} == {
            "metrics.active_days",
            "metrics.win_rate_pct",
        }
        assert "零权重持仓缺陷" in payload["hint"]

    def test_last_row_skipped_without_next_day(self, rotation: Any) -> None:
        """库里找不到最后一行的窗口终点：最后一行不核对逐日收益并写明原因。"""
        outcome, bars, _model = rotation
        payload = _service(outcome, bars, has_next_day=False).verify("bt-verify")
        assert payload["checked_days"] == len(outcome["daily_rows"]) - 1
        assert any("最后一行" in note for note in payload["skipped"])

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"status": "failed"}, "已成功完成"),
            ({"params": {"_composite_model": "member_nav_v1"}}, "组合策略"),
            ({"params": {"_execution_model": "t_plus_1_open"}}, "constant_mix_v0"),
        ],
    )
    def test_rejects_unsupported(
        self, rotation: Any, overrides: dict[str, Any], message: str
    ) -> None:
        """未成功、组合策略、旧记账口径的回测不做对账。"""
        outcome, bars, _model = rotation
        with pytest.raises(ValueError, match=message):
            _service(outcome, bars, **overrides).verify("bt-verify")

    def test_missing_backtest(self, rotation: Any) -> None:
        """回测不存在时抛 LookupError。"""
        outcome, bars, _model = rotation
        with pytest.raises(LookupError):
            _service(outcome, bars).verify("bt-missing")
