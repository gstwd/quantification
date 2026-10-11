"""回测记账口径（drift_v1）与回测层修复的回归测试。

覆盖：
- 执行器：非调仓日持仓随行情漂移；调仓日两段复利、换手按漂移后的开盘持仓计算；
  T+1 收盘模式按漂移后的持仓计成交换手；
- 收益行情前向填充：缺口期间的涨跌在缺口后补回，而不是永久丢失；
- 主循环：params 口径元数据以新 dict 赋值（JSON 列不追踪原地修改）、不落终端行、
  共享行情缓存不被就地污染、指数结果按 checkpoint 批量写入；
- 创建回测写库失败时上抛、不留验证期消费痕迹；
- 回撤持续天数不把"持平于峰值"的空仓日计入；
- 引擎回测模式跳过管线调试详情。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from quant_etf_api.domain.portfolio.execution import (
    ACCOUNTING_MODEL_DRIFT,
    ACCOUNTING_MODEL_LEGACY,
    BacktestExecutor,
    drift_weights,
    forward_fill_return_bars,
)
from quant_etf_api.domain.research.metrics import compute_performance_metrics
from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.factors.compute import FactorMatrix
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.services.backtest_service import BacktestRunCaches, BacktestService

D0, D1, D2, D3 = date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)


def _bar(close: float | None, open_price: float | None = None) -> SimpleNamespace:
    """构造最小行情对象（开盘价缺省等于收盘价）。"""
    return SimpleNamespace(
        close_price=close,
        open_price=close if open_price is None else open_price,
        high_price=close,
        low_price=close,
    )


class TestDriftWeights:
    """权重漂移纯函数。"""

    def test_weights_follow_relative_performance(self) -> None:
        """a 涨 10%、b 跌 10% 时，a 的权重上升、b 下降，现金占比随组合收益变化。"""
        drifted = drift_weights({"a": 0.5, "b": 0.5}, {"a": 10.0, "b": -10.0}, 0.0)
        assert drifted["a"] == pytest.approx(0.55)
        assert drifted["b"] == pytest.approx(0.45)

    def test_cash_weight_shrinks_when_assets_rise(self) -> None:
        """半仓持有上涨 10% 的资产：资产权重 0.55 / 1.05。"""
        drifted = drift_weights({"a": 0.5}, {"a": 10.0}, 5.0)
        assert drifted["a"] == pytest.approx(0.55 / 1.05)

    def test_missing_return_treated_as_flat(self) -> None:
        """资产收益缺失按 0 处理，与组合收益口径一致。"""
        drifted = drift_weights({"a": 0.5, "b": 0.5}, {"a": None, "b": 10.0}, 5.0)
        assert drifted["a"] == pytest.approx(0.5 / 1.05)
        assert drifted["b"] == pytest.approx(0.55 / 1.05)


class TestExecutorOpenModel:
    """T+1 开盘执行模型。"""

    def _bars(self) -> dict[tuple[str, date], Any]:
        """a 每日 +10%，b 每日 -10%（开盘价 = 前收盘，隔夜段为 0）。"""
        bars: dict[tuple[str, date], Any] = {}
        a, b = 100.0, 100.0
        for day in (D0, D1, D2, D3):
            bars[("a", day)] = _bar(a, a / 1.1 if day != D0 else a)
            bars[("b", day)] = _bar(b, b / 0.9 if day != D0 else b)
            a, b = a * 1.1, b * 0.9
        return bars

    def test_hold_days_drift_and_rebalance_turnover_uses_drifted_weights(self) -> None:
        """两次调仓之间持仓漂移；再次调回 50/50 时换手按漂移后的权重计算。"""
        bars = self._bars()
        executor = BacktestExecutor("t_plus_1_open")
        target = {"a": 0.5, "b": 0.5}
        # D0 建仓（D1 开盘成交，日内段 a +10% / b -10%）
        first = executor.step(
            trade_date=D0,
            next_date=D1,
            target_positions=target,
            should_rebalance=True,
            return_bars=bars,
            quality_bars=bars,
        )
        assert first.portfolio_return == pytest.approx(0.0, abs=1e-4)
        assert first.turnover == pytest.approx(0.5)
        # D1 收盘：权重已漂移到 0.55 / 0.45
        assert executor.holdings["a"] == pytest.approx(0.55)
        # D1 不调仓：收益按漂移后的权重计算，不再"免费"回到 50/50
        hold = executor.step(
            trade_date=D1,
            next_date=D2,
            target_positions={},
            should_rebalance=False,
            return_bars=bars,
            quality_bars=bars,
        )
        assert hold.positions["a"] == pytest.approx(0.55)
        assert hold.portfolio_return == pytest.approx(0.55 * 10 - 0.45 * 10, abs=1e-3)
        # D2 调回 50/50：换手 = 漂移后权重与目标之差（历史口径记 0）
        rebalance = executor.step(
            trade_date=D2,
            next_date=D3,
            target_positions=target,
            should_rebalance=True,
            return_bars=bars,
            quality_bars=bars,
        )
        drifted_a = 0.55 * 1.1 / (1 + (0.55 * 0.1 - 0.45 * 0.1))
        assert rebalance.turnover == pytest.approx(drifted_a - 0.5, abs=1e-4)
        assert rebalance.turnover > 0

    def test_rebalance_day_legs_compound(self) -> None:
        """调仓日隔夜段与日内段按复利衔接。"""
        bars = {
            ("a", D0): _bar(100.0),
            ("a", D1): _bar(104.0, 102.0),
        }
        executor = BacktestExecutor("t_plus_1_open")
        executor.step(
            trade_date=D0,
            next_date=D1,
            target_positions={"a": 1.0},
            should_rebalance=True,
            return_bars=bars,
            quality_bars=bars,
        )
        bars[("a", D2)] = _bar(110.0, 105.0)
        day = executor.step(
            trade_date=D1,
            next_date=D2,
            target_positions={"a": 1.0},
            should_rebalance=True,
            return_bars=bars,
            quality_bars=bars,
        )
        # 满仓同一资产：(105/104) × (110/105) - 1 = 110/104 - 1
        assert day.portfolio_return == pytest.approx((110 / 104 - 1) * 100, abs=1e-3)


class TestExecutorCloseModel:
    """T+1 收盘执行模型。"""

    def test_fill_turnover_uses_drifted_holdings(self) -> None:
        """收盘成交的换手按漂移到成交日收盘的实际持仓计算。"""
        bars = {
            ("a", D0): _bar(100.0),
            ("a", D1): _bar(100.0),
            ("a", D2): _bar(120.0),
            ("a", D3): _bar(120.0),
        }
        executor = BacktestExecutor("t_plus_1_close")
        steps = [(D0, D1, True), (D1, D2, False), (D2, D3, True)]
        results = [
            executor.step(
                trade_date=day,
                next_date=nxt,
                target_positions={"a": 0.5},
                should_rebalance=rebalance,
                return_bars=bars,
                quality_bars=bars,
            )
            for day, nxt, rebalance in steps
        ]
        # D0 指令在 D1 收盘成交（建仓 0.25），D1→D2 a 涨 20%，半仓收益 10%
        assert results[1].turnover == pytest.approx(0.25)
        assert results[1].portfolio_return == pytest.approx(10.0)
        # D2 无待成交指令：持仓已漂移到 0.6/1.1
        assert results[2].turnover == 0.0
        assert results[2].positions["a"] == pytest.approx(0.6 / 1.1)


class TestForwardFillReturnBars:
    """收益结算行情的前向填充。"""

    def test_gap_move_is_recovered_after_gap(self) -> None:
        """D1 缺行情：D0 段记 0，D1 段相对 D0 收盘结算，累计收益不丢失。"""
        bars = {("a", D0): _bar(100.0), ("a", D2): _bar(121.0)}
        filled = forward_fill_return_bars(bars, ["a"], [D0, D1, D2])
        executor = BacktestExecutor("t_plus_1_open")
        executor.step(
            trade_date=D0,
            next_date=D1,
            target_positions={"a": 1.0},
            should_rebalance=True,
            return_bars=filled,
            quality_bars=bars,
        )
        day = executor.step(
            trade_date=D1,
            next_date=D2,
            target_positions={},
            should_rebalance=False,
            return_bars=filled,
            quality_bars=bars,
        )
        assert day.portfolio_return == pytest.approx(21.0)
        # 缺口统计仍看原始行情
        assert day.missing_bar_count == 1

    def test_no_fill_before_first_valid_close(self) -> None:
        """指数尚未发布（从未有过有效收盘价）时不填充。"""
        bars = {("a", D1): _bar(100.0)}
        filled = forward_fill_return_bars(bars, ["a"], [D0, D1])
        assert ("a", D0) not in filled

    def test_missing_open_uses_close(self) -> None:
        """开盘价缺失时合成 open=close，旧仓位隔夜段吃满全天。"""
        bars = {("a", D0): _bar(100.0, 0.0)}
        filled = forward_fill_return_bars(bars, ["a"], [D0])
        assert filled[("a", D0)].open_price == 100.0


def _loop_service(
    dates: list[date], bars: dict[tuple[str, date], Any]
) -> tuple[BacktestService, BacktestRunModel, list[Any]]:
    """构造可在内存中跑主循环的服务：单资产 a 满仓、每日调仓。"""
    svc = BacktestService(db=MagicMock())
    svc._ensure_market_scope_bars = MagicMock()
    svc._get_lookback_days = MagicMock(return_value=90)
    svc._filter_non_trading_dates = MagicMock(side_effect=lambda s, e, d: (list(d), []))
    universe = [{"index_code": "a", "name_cn": "a", "category": "broad_index", "is_active": True}]
    svc._prepare_backtest_data = MagicMock(
        return_value=(universe, ["a"], list(dates), bars, {}, {})
    )
    svc._factor_provider = MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = FactorMatrix(
        values={d: {("a", "return_5d"): 1.0} for d in dates}
    )
    svc._factor_provider.instance_fingerprint.return_value = ()
    svc._factor_provider.market_shape_refs.return_value = set()
    svc._context_builder = MagicMock()
    svc._context_builder.build.side_effect = lambda config, trade_date, **kw: EngineContext(
        trade_date=trade_date,
        universe=[{"index_code": c, "name_cn": c} for c in kw.get("index_codes") or []],
    )

    def _run_engine(
        config: StrategyConfig, context: EngineContext, include_details: bool = False
    ) -> EngineResult:
        """桩引擎：候选池非空时满仓 a。"""
        positions = {"a": 1.0} if context.universe else {}
        return EngineResult(
            trade_date=context.trade_date,
            strategy_id=config.strategy_id,
            timing=None,
            scores={"a": 1.0} if positions else {},
            rankings=[],
            positions=positions,
            total_exposure=sum(positions.values()),
            cash_ratio=1.0 - sum(positions.values()),
            strategy_results=[],
            timing_target_exposure=1.0,
        )

    svc._engine = MagicMock()
    svc._engine.run.side_effect = _run_engine
    collected: list[Any] = []
    svc._backtest_repo = MagicMock()
    svc._backtest_repo.add_daily_result.side_effect = collected.append
    row = BacktestRunModel(
        backtest_id="bt-drift",
        strategy_id="t_drift",
        start_date=dates[0],
        end_date=dates[-1],
        universe_filter={"mode": "subset", "index_codes": ["a"]},
        params={
            "_execution_model": "t_plus_1_open",
            "_data_quality_mode": "warn",
            "_enable_benchmark": False,
        },
        status="running",
    )
    return svc, row, collected


def _config() -> StrategyConfig:
    """单资产、每日调仓的最小策略配置。"""
    return StrategyConfig(
        strategy_id="t_drift",
        display_name="t_drift",
        score=ScoreConfig(factors={"return_5d": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight", default_exposure=1.0),
    )


class TestLoopFixes:
    """回测主循环层面的修复。"""

    def _bars(self) -> dict[tuple[str, date], Any]:
        """a 每日 +10%。"""
        return {
            ("a", D0): _bar(100.0),
            ("a", D1): _bar(110.0),
            ("a", D2): _bar(121.0),
            ("a", D3): _bar(133.1),
        }

    def test_params_reassigned_as_new_dict_with_metadata(self) -> None:
        """口径元数据写在新 dict 上整体赋值：原 dict 不被原地修改（否则 JSON 列不落库）。"""
        svc, row, _ = _loop_service([D0, D1, D2, D3], self._bars())
        original = row.params
        snapshot = dict(original)
        svc._run_backtest_loop("bt-drift", row, _config())
        assert original == snapshot
        assert row.params is not original
        assert row.params["_warmup_trading_days"] == 0
        assert row.params["_lookback_days"] == 90
        assert row.params["_benchmark_alignment"] == "decision_day"
        assert row.params["_accounting_model"] == ACCOUNTING_MODEL_DRIFT

    def test_no_terminal_row(self) -> None:
        """最后一个交易日没有收益窗口：逐日行只覆盖决策日，指标不含人为 0 收益样本。"""
        svc, row, rows = _loop_service([D0, D1, D2, D3], self._bars())
        svc._run_backtest_loop("bt-drift", row, _config())
        assert [r.trade_date for r in rows] == [D0, D1, D2]
        assert all(r.portfolio_return != 0.0 for r in rows[1:])
        metrics = svc._backtest_repo.mark_success.call_args.args[1]
        assert metrics["total_trading_days"] == 3

    def test_single_trading_day_rejected(self) -> None:
        """有效交易日不足 2 个时明确报错，而不是在日志里抛 IndexError。"""
        svc, row, _ = _loop_service([D0], {("a", D0): _bar(100.0)})
        with pytest.raises(ValueError, match="不足 2 个"):
            svc._run_backtest_loop("bt-drift", row, _config())

    def test_shared_cache_not_mutated(self) -> None:
        """批量缓存的行情快照不被补充基准行情等操作就地污染。"""
        dates = [D0, D1, D2, D3]
        svc, row, _ = _loop_service(dates, self._bars())
        row.params = {**row.params, "_enable_benchmark": True, "_benchmark_index_code": "b"}
        cached_bars = self._bars()
        svc._prepare_backtest_data = MagicMock(
            return_value=([{"index_code": "a", "name_cn": "a"}], ["a"], dates, cached_bars, {}, {})
        )
        svc._load_all_index_bars = MagicMock(return_value={("b", D0): _bar(1.0)})
        svc._run_backtest_loop("bt-drift", row, _config(), caches=BacktestRunCaches())
        assert ("b", D0) not in cached_bars

    def test_index_results_bulk_inserted_at_flush(self) -> None:
        """指数级结果按 checkpoint / 收尾批量写入，不再逐行登记 ORM 对象。"""
        svc, row, _ = _loop_service([D0, D1, D2, D3], self._bars())
        svc._run_backtest_loop("bt-drift", row, _config())
        inserted = [
            item
            for call in svc._backtest_repo.bulk_insert_index_results.call_args_list
            for item in call.args[0]
        ]
        assert [item["trade_date"] for item in inserted] == [D0, D1, D2]
        assert svc._backtest_repo.add_index_result.call_count == 0

    def test_legacy_rows_read_as_constant_mix(self) -> None:
        """缺少记账口径指纹的存量回测按 constant_mix_v0 标注。"""
        svc = BacktestService(db=MagicMock())
        row = BacktestRunModel(
            backtest_id="bt-legacy",
            strategy_id="t_drift",
            start_date=D0,
            end_date=D2,
            universe_filter={"mode": "all"},
            params={"_cost_bps": 10.0, "_enable_benchmark": False},
            status="success",
        )
        daily_rows = [
            SimpleNamespace(
                portfolio_return=0.1,
                trade_date=d,
                benchmark_return=None,
                turnover=None,
                total_exposure=1.0,
                positions={"a": 1.0},
            )
            for d in (D0, D1, D2)
        ]
        assert svc._compute_stability(row, daily_rows).accounting_model == ACCOUNTING_MODEL_LEGACY


class TestCreateBacktestInsertFailure:
    """创建回测写库失败。"""

    def test_insert_failure_raises_and_skips_validation_mark(self, monkeypatch) -> None:
        """写库失败必须上抛，且不得留下验证期消费痕迹。"""
        marked: list[str] = []

        class _FakeConfigService:
            def __init__(self, db: object) -> None:
                self._db = db

            def get_parsed_config(self, strategy_id: str) -> StrategyConfig:
                return _config()

            def validate_parsed(self, config: StrategyConfig) -> object:
                return type("R", (), {"valid": True, "errors": []})()

            def get_config(self, strategy_id: str) -> None:
                return None

            def mark_validation_consumed(self, strategy_id: str, note: str) -> None:
                marked.append(strategy_id)

        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.StrategyConfigService",
            _FakeConfigService,
        )
        db = MagicMock()
        db.commit.side_effect = RuntimeError("db down")
        svc = BacktestService(db=db)
        with pytest.raises(RuntimeError, match="db down"):
            svc.create_backtest(
                BacktestCreateRequest(
                    strategy_id="t_drift",
                    start_date=date(2026, 1, 5),
                    end_date=date(2026, 3, 31),
                    purpose="validation",
                    purpose_reason="否决检查",
                )
            )
        db.rollback.assert_called()
        assert marked == []


class TestDrawdownDuration:
    """回撤持续天数口径。"""

    def test_flat_days_at_peak_are_not_underwater(self) -> None:
        """创新高后空仓 0 收益的日子不是水下，不计入回撤持续天数。"""
        metrics = compute_performance_metrics([1.0, 0.0, 0.0, 0.0, 0.0])
        assert metrics.max_drawdown_days == 0

    def test_underwater_days_still_counted(self) -> None:
        """真实水下的日子照常计数。"""
        metrics = compute_performance_metrics([1.0, -1.0, 0.0, 0.5])
        assert metrics.max_drawdown_days == 3


class TestEngineBacktestMode:
    """引擎回测模式。"""

    def test_backtest_mode_skips_pipeline_detail(self) -> None:
        """include_details=False 时不构建管线调试详情。"""
        config = _config()
        context = EngineContext(
            trade_date=D0,
            universe=[{"index_code": "a", "name_cn": "a"}],
            asset_factors={("a", "return_5d"): 1.0},
        )
        engine = StrategyEngine()
        assert engine.run(config, context, include_details=False).pipeline_detail is None
        assert engine.run(config, context, include_details=True).pipeline_detail is not None
