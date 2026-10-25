"""路径依赖功能测试：风险模型、风控叠加层、偏离调仓、排名缓冲带与新的仓位分配方法。

覆盖：
1. 收益面板与协方差、风险平价权重（等风险贡献）；
2. 叠加层：移动止损冷却、回撤止损封顶与重置、波动率目标缩放；
3. 当日目标合成：未启用时恒等、偏离触发再平衡、小额换手跳过；
4. 排名缓冲带与 inverse_vol / risk_parity 分配器；
5. 配置校验与旧平铺调仓配置升级保留 band；
6. 回测主循环端到端：移动止损在下一决策日强制清出。
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import numpy as np
import pytest
from pydantic import ValidationError

from quant_etf_api.domain.portfolio.risk_model import (
    ReturnPanel,
    annualized_volatility,
    covariance,
    risk_parity_weights,
)
from quant_etf_api.domain.strategies.models import AssetRanking
from quant_etf_api.domain.strategies.risk_overlay import (
    RiskOverlayState,
    advance_overlay_state,
    apply_risk_overlay,
    max_weight_drift,
    resolve_day_target,
)
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import (
    DrawdownStopConfig,
    PortfolioConfig,
    RankConfig,
    RebalanceBandConfig,
    RebalanceConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
    TrailingStopConfig,
    VolTargetConfig,
)
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.orchestrator import StrategyEngine, decision_required_refs
from quant_etf_api.engine.portfolio import build_allocator
from quant_etf_api.engine.rank import DefaultRankEngine
from quant_etf_api.factors.compute import FactorMatrix
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService

_DAY = date(2025, 3, 3)


def _bar(close: float) -> SimpleNamespace:
    """构造全价相同的日线。"""
    return SimpleNamespace(close_price=close, open_price=close, high_price=close, low_price=close)


# ── 1. 风险模型 ───────────────────────────────────────────────────────────


class TestRiskModel:
    """收益面板、协方差与风险平价。"""

    def test_panel_returns_and_window(self) -> None:
        """收盘到收盘收益；缺价格的格子为 NaN；窗口按末日截取。"""
        days = [_DAY + timedelta(days=i) for i in range(4)]
        bars = {("A", d): _bar(c) for d, c in zip(days, [100, 110, 99, 99])}
        bars[("B", days[0])] = _bar(10)
        bars[("B", days[2])] = _bar(11)
        panel = ReturnPanel.from_bars(bars, ["A", "B"])
        assert panel.returns[1, 0] == pytest.approx(0.1)
        assert np.isnan(panel.returns[1, 1])
        block = panel.window(days[3], 2, ["A", "B", "C"])
        assert block is not None and block.shape == (2, 3)
        assert np.isnan(block[:, 2]).all()
        assert panel.window(_DAY - timedelta(days=1), 2, ["A"]) is None
        # 非交易日按不晚于它的最近交易日截取
        later = panel.window(days[3] + timedelta(days=2), 1, ["A"])
        assert later is not None and later[0, 0] == pytest.approx(0.0)

    def test_covariance_requires_rows(self) -> None:
        """有效天数不足时返回 None。"""
        block = np.array([[0.01, 0.02], [np.nan, 0.01], [0.02, -0.01]])
        assert covariance(block, 3) is None
        assert covariance(block, 2) is not None

    def test_risk_parity_equalizes_contributions(self) -> None:
        """等风险贡献：w_i (Σw)_i 全部相等，且低波动资产权重更高。"""
        cov = np.array([[0.04, 0.006, 0.0], [0.006, 0.01, 0.002], [0.0, 0.002, 0.0225]])
        weights = risk_parity_weights(cov)
        assert weights is not None
        assert weights.sum() == pytest.approx(1.0)
        contributions = weights * (cov @ weights)
        assert np.allclose(contributions, contributions.mean(), rtol=1e-6)
        assert weights[1] > weights[2] > weights[0]

    def test_risk_parity_rejects_bad_cov(self) -> None:
        """非正方差返回 None；单资产为 1。"""
        assert risk_parity_weights(np.array([[0.0]])) is None
        single = risk_parity_weights(np.array([[0.04]]))
        assert single is not None and single.tolist() == [1.0]

    def test_annualized_volatility(self) -> None:
        """年化波动 = sqrt(wᵀΣw × 252)。"""
        cov = np.array([[0.0001]])
        assert annualized_volatility({"A": 1.0}, cov, ["A"]) == pytest.approx(0.01 * 252**0.5)


# ── 2. 叠加层 ─────────────────────────────────────────────────────────────


class TestRiskOverlay:
    """回撤止损、移动止损、波动率目标。"""

    def test_cooldown_asset_removed_to_cash(self) -> None:
        """冷却中的资产被清出，权重留作现金不再分配。"""
        risk = RiskConfig(trailing_stop=TrailingStopConfig(threshold=0.1))
        state = RiskOverlayState(asset_cooldowns={"A": 3}, force_rebalance=True)
        result = apply_risk_overlay({"A": 0.5, "B": 0.5}, state, risk)
        assert result.positions == {"B": 0.5}
        assert result.removed == ("A",)
        assert result.force is True

    def test_drawdown_stop_caps_exposure(self) -> None:
        """止损期间总仓位封顶。"""
        risk = RiskConfig(drawdown_stop=DrawdownStopConfig(threshold=0.1, exposure=0.2))
        result = apply_risk_overlay({"A": 0.6, "B": 0.2}, RiskOverlayState(stop_days_left=2), risk)
        assert sum(result.positions.values()) == pytest.approx(0.2)
        assert result.positions["A"] == pytest.approx(0.15)

    def test_vol_target_scales_down_only(self) -> None:
        """事前波动超过目标时按比例降仓，低于目标时不加仓。"""
        risk = RiskConfig(vol_target=VolTargetConfig(target=0.10))
        high = apply_risk_overlay(
            {"A": 1.0}, RiskOverlayState(), risk, lambda codes: np.array([[0.0004]])
        )
        # 年化波动 0.02×√252 ≈ 0.3175 → 缩到 0.10
        assert sum(high.positions.values()) == pytest.approx(0.10 / (0.02 * 252**0.5), abs=1e-4)
        low = apply_risk_overlay(
            {"A": 0.5}, RiskOverlayState(), risk, lambda codes: np.array([[0.000001]])
        )
        assert low.positions == {"A": 0.5}

    def test_drawdown_trigger_cooldown_and_reset(self) -> None:
        """回撤触发 → 冷却 N 日 → 到期以当时净值重置峰值。"""
        risk = RiskConfig(drawdown_stop=DrawdownStopConfig(threshold=0.1, cooldown_days=2))
        state = RiskOverlayState()
        kwargs: dict[str, Any] = {"held": {}, "start_closes": {}, "end_closes": {}}
        state = advance_overlay_state(state, risk, portfolio_return_pct=-12.0, **kwargs)
        assert (state.stop_days_left, state.force_rebalance) == (2, True)
        state = advance_overlay_state(state, risk, portfolio_return_pct=0.0, **kwargs)
        assert (state.stop_days_left, state.force_rebalance) == (1, False)
        state = advance_overlay_state(state, risk, portfolio_return_pct=1.0, **kwargs)
        assert state.stop_days_left == 0
        assert state.peak_nav == pytest.approx(state.nav)

    def test_trailing_stop_tracks_peak_since_entry(self) -> None:
        """入场价作为初始高点；从高点回撤达到阈值即冷却并要求次日成交。"""
        risk = RiskConfig(trailing_stop=TrailingStopConfig(threshold=0.1, cooldown_days=2))
        state = advance_overlay_state(
            RiskOverlayState(),
            risk,
            portfolio_return_pct=0.0,
            held={"A": 0.5},
            start_closes={"A": 100.0},
            end_closes={"A": 120.0},
        )
        assert state.asset_peaks == {"A": 120.0}
        state = advance_overlay_state(
            state,
            risk,
            portfolio_return_pct=0.0,
            held={"A": 0.5},
            start_closes={"A": 120.0},
            end_closes={"A": 107.0},
        )
        assert state.asset_cooldowns == {"A": 2}
        assert state.asset_peaks == {}
        assert state.force_rebalance is True

    def test_max_weight_drift(self) -> None:
        """最大单资产偏离含新增 / 消失的资产。"""
        assert max_weight_drift({"A": 0.6, "B": 0.4}, {"A": 0.5, "C": 0.1}) == pytest.approx(0.4)


# ── 3. 当日目标合成 ───────────────────────────────────────────────────────


class TestResolveDayTarget:
    """偏离调仓与叠加层的合成顺序。"""

    def test_identity_without_features(self) -> None:
        """未启用偏离调仓与叠加层时原样返回。"""
        result = resolve_day_target(
            {"A": 0.5},
            legs_due=False,
            base={"A": 0.7},
            last_target={"A": 0.5},
            risk=RiskConfig(),
            band=None,
            overlay_state=None,
        )
        assert (result.positions, result.should_rebalance, result.reason) == (
            {"A": 0.5},
            False,
            None,
        )

    def test_band_drift_rebalances_to_last_target(self) -> None:
        """非调仓日偏离超阈值 → 回到最近目标。"""
        result = resolve_day_target(
            {"A": 0.7, "B": 0.3},
            legs_due=False,
            base={"A": 0.7, "B": 0.3},
            last_target={"A": 0.5, "B": 0.5},
            risk=None,
            band=RebalanceBandConfig(max_drift=0.1),
            overlay_state=None,
        )
        assert result.should_rebalance and result.reason == "band_drift"
        assert result.positions == {"A": 0.5, "B": 0.5}

    def test_band_skips_small_turnover(self) -> None:
        """调仓日所需换手低于阈值时跳过；空仓建仓不跳过。"""
        band = RebalanceBandConfig(min_turnover=0.05)
        skipped = resolve_day_target(
            {"A": 0.52, "B": 0.48},
            legs_due=True,
            base={"A": 0.5, "B": 0.5},
            last_target=None,
            risk=None,
            band=band,
            overlay_state=None,
        )
        assert (skipped.should_rebalance, skipped.reason) == (False, "band_skip")
        first = resolve_day_target(
            {"A": 0.02},
            legs_due=True,
            base={},
            last_target=None,
            risk=None,
            band=band,
            overlay_state=None,
        )
        assert first.should_rebalance is True

    def test_risk_stop_forces_trade(self) -> None:
        """新触发止损在非调仓日强制成交，且不被小额换手规则跳过。"""
        risk = RiskConfig(trailing_stop=TrailingStopConfig(threshold=0.1))
        result = resolve_day_target(
            {"A": 0.02, "B": 0.5},
            legs_due=False,
            base={"A": 0.02, "B": 0.5},
            last_target=None,
            risk=risk,
            band=RebalanceBandConfig(min_turnover=0.5),
            overlay_state=RiskOverlayState(asset_cooldowns={"A": 1}, force_rebalance=True),
        )
        assert (result.should_rebalance, result.reason) == (True, "risk_stop")
        assert result.positions == {"B": 0.5}


# ── 4. 排名缓冲带与分配器 ─────────────────────────────────────────────────


def _context(**fields: Any) -> EngineContext:
    """构造只含必要字段的引擎上下文。"""
    return EngineContext(trade_date=_DAY, universe=[], **fields)


class TestRankBuffer:
    """排名缓冲带保留老持仓。"""

    def test_keeps_holding_within_buffer(self) -> None:
        """老持仓排第 3（≤ top_n + buffer）时保留，挤掉第 2 名。"""
        scores = {"A": 9.0, "B": 8.0, "C": 7.0, "D": 6.0}
        config = RankConfig(top_n=2, buffer_n=1)
        held = _context(current_holdings={"C": 0.5, "D": 0.5})
        ranked = DefaultRankEngine().rank(config, scores, held)
        assert [r.index_code for r in ranked] == ["A", "C"]

    def test_without_holdings_is_plain_top_n(self) -> None:
        """无持仓信息时与严格前 N 名一致。"""
        scores = {"A": 9.0, "B": 8.0, "C": 7.0}
        ranked = DefaultRankEngine().rank(RankConfig(top_n=2, buffer_n=3), scores, _context())
        assert [r.index_code for r in ranked] == ["A", "B"]


class TestNewAllocators:
    """inverse_vol 与 risk_parity。"""

    def _rankings(self, codes: list[str]) -> list[AssetRanking]:
        return [AssetRanking(index_code=c, name_cn=c, category="", score=1.0) for c in codes]

    def test_inverse_vol(self) -> None:
        """权重 ∝ 1/vol，总和为目标仓位。"""
        config = PortfolioConfig(method="inverse_vol", vol_factor="vol", default_exposure=0.9)
        context = _context(asset_factors={("A", "vol"): 10.0, ("B", "vol"): 30.0})
        positions = build_allocator("inverse_vol").allocate(
            config, self._rankings(["A", "B"]), None, context
        )
        assert positions == {"A": 0.675, "B": 0.225}

    def test_risk_parity_and_fallback(self) -> None:
        """协方差可估时按等风险贡献；历史不足退回等权。"""
        days = [_DAY + timedelta(days=i) for i in range(80)]
        rng = np.random.default_rng(3)
        closes = {"A": 100.0, "B": 100.0}
        bars = {}
        for day in days:
            closes["A"] *= 1 + rng.normal(0, 0.02)
            closes["B"] *= 1 + rng.normal(0, 0.005)
            bars[("A", day)] = _bar(closes["A"])
            bars[("B", day)] = _bar(closes["B"])
        panel = ReturnPanel.from_bars(bars, ["A", "B"])
        config = PortfolioConfig(method="risk_parity", cov_window=60, default_exposure=1.0)
        allocator = build_allocator("risk_parity")
        context = EngineContext(trade_date=days[-1], universe=[], return_panel=panel)
        positions = allocator.allocate(config, self._rankings(["A", "B"]), None, context)
        assert sum(positions.values()) == pytest.approx(1.0, abs=1e-3)
        assert positions["B"] > positions["A"] * 2
        early = EngineContext(trade_date=days[5], universe=[], return_panel=panel)
        assert allocator.allocate(config, self._rankings(["A", "B"]), None, early) == {
            "A": 0.5,
            "B": 0.5,
        }

    def test_inverse_vol_factor_is_decision_required(self) -> None:
        """波动率因子缺失的资产在评分前剔除。"""
        config = StrategyConfig(
            strategy_id="s",
            display_name="s",
            score=ScoreConfig(factors={"val": 1.0}),
            portfolio=PortfolioConfig(method="inverse_vol", vol_factor="vol"),
        )
        assert "vol" in decision_required_refs(config)


# ── 5. 配置校验 ───────────────────────────────────────────────────────────


class TestConfig:
    """新增字段的校验与派生属性。"""

    def test_validation_rules(self) -> None:
        """buffer_n 需 top_n；inverse_vol 需 vol_factor；band 至少一项。"""
        with pytest.raises(ValidationError):
            RankConfig(buffer_n=2)
        with pytest.raises(ValidationError):
            PortfolioConfig(method="inverse_vol")
        with pytest.raises(ValidationError):
            RebalanceBandConfig()

    def test_legacy_flat_rebalance_keeps_band(self) -> None:
        """旧平铺调仓配置升级为双腿时保留 band。"""
        parsed = RebalanceConfig.model_validate(
            {"frequency": "weekly", "day_of_week": 4, "band": {"min_turnover": 0.05}}
        )
        assert parsed.selection.frequency == "weekly"
        assert parsed.band is not None and parsed.band.min_turnover == 0.05

    def test_path_state_and_return_window(self) -> None:
        """路径依赖与收益窗口的派生属性。"""
        base: dict[str, Any] = {
            "strategy_id": "s",
            "display_name": "s",
            "score": ScoreConfig(factors={"val": 1.0}),
        }
        plain = StrategyConfig(**base)
        assert (plain.needs_path_state, plain.return_window) == (False, None)
        rich = StrategyConfig(
            **base,
            rank=RankConfig(top_n=2, buffer_n=1),
            portfolio=PortfolioConfig(method="risk_parity", cov_window=90),
            risk=RiskConfig(vol_target=VolTargetConfig(target=0.1, window=120)),
        )
        assert (rich.needs_path_state, rich.return_window) == (True, 120)


# ── 6. 回测主循环端到端 ───────────────────────────────────────────────────

_LOOP_DATES = [d for d in (_DAY + timedelta(days=i) for i in range(14)) if d.weekday() < 5][:8]


def _loop_service(closes_a: list[float]) -> BacktestService:
    """两资产内存回测：A 按给定收盘价走、B 恒定；评分因子相同。"""
    svc = BacktestService(db=mock.MagicMock())
    svc._ensure_market_scope_bars = mock.MagicMock()
    svc._get_lookback_days = mock.MagicMock(return_value=90)
    svc._write_index_results = mock.MagicMock(return_value=(0, 0))
    bars: dict[tuple[str, date], Any] = {}
    for day, close in zip(_LOOP_DATES, closes_a):
        bars[("A", day)] = _bar(close)
        bars[("B", day)] = _bar(100.0)
    universe = [
        {"index_code": code, "name_cn": code, "category": "broad_index"} for code in ("A", "B")
    ]
    svc._prepare_backtest_data = mock.MagicMock(
        return_value=(universe, ["A", "B"], list(_LOOP_DATES), bars, {}, {})
    )
    values = {d: {("A", "val"): 1.0, ("B", "val"): 1.0} for d in _LOOP_DATES}
    svc._factor_provider = mock.MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = FactorMatrix(values=values)
    svc._factor_provider.market_shape_refs.return_value = []
    svc._context_builder = ContextBuilder(mock.MagicMock(), factor_provider=svc._factor_provider)
    svc._engine = StrategyEngine()
    svc._backtest_repo = mock.MagicMock()
    return svc


def _loop_row(model: str) -> BacktestRunModel:
    """构造运行中的回测行。"""
    return BacktestRunModel(
        backtest_id="bt-path",
        strategy_id="path",
        start_date=_LOOP_DATES[0],
        end_date=_LOOP_DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["A", "B"]},
        params={"_execution_model": model, "_data_quality_mode": "warn"},
        status="running",
    )


def _loop_config(**overrides: Any) -> StrategyConfig:
    """等权持有两资产、月度调仓（主循环内不再有日历调仓）。"""
    fields: dict[str, Any] = {
        "strategy_id": "path",
        "display_name": "path",
        "score": ScoreConfig(factors={"val": 1.0}),
        "rank": RankConfig(top_n=2),
        "portfolio": PortfolioConfig(method="equal_weight", default_exposure=1.0),
        "risk": RiskConfig(max_asset_weight=1.0),
        "rebalance": RebalanceConfig.model_validate({"frequency": "monthly", "day_of_month": 3}),
    }
    fields.update(overrides)
    return StrategyConfig(**fields)


class TestLoopIntegration:
    """移动止损在回测主循环里的时序。"""

    def test_trailing_stop_forces_exit_next_decision(self) -> None:
        """A 在第 3 日收盘较入场高点跌 20% → 下一决策日强制清出 A，B 不变。"""
        closes_a = [100, 110, 88, 88, 88, 88, 88, 88]
        risk = RiskConfig(
            max_asset_weight=1.0,
            trailing_stop=TrailingStopConfig(threshold=0.15, cooldown_days=10),
        )
        svc = _loop_service(closes_a)
        outcome = svc._run_backtest_loop(
            "bt-path", _loop_row("t_plus_1_open"), _loop_config(risk=risk)
        )
        executions = [day for _d, day in outcome["executions"]]
        assert executions[0].executed_positions == {"A": 0.5, "B": 0.5}
        # 第 2 个窗口 [d1, d2] 收盘触发；d2 决策强制成交，T+1 开盘后只剩 B
        assert "A" not in executions[2].executed_positions
        assert executions[2].turnover > 0
        assert outcome["params"]["_risk_overlay_model"] == "overlay_v1"
        state = outcome["path_state"]
        assert state.overlay_state is not None and "A" in state.overlay_state.asset_cooldowns

    def test_no_overlay_keeps_legacy_params(self) -> None:
        """未启用新功能时不写叠加层口径键，持仓不变。"""
        svc = _loop_service([100, 110, 88, 88, 88, 88, 88, 88])
        outcome = svc._run_backtest_loop("bt-path", _loop_row("t_plus_1_close"), _loop_config())
        assert "_risk_overlay_model" not in outcome["params"]
        executions = [day for _d, day in outcome["executions"]]
        assert all(
            set(day.executed_positions) == {"A", "B"} for day in executions[1:]
        )


class TestLiveReplayDecision:
    """实时决策在路径依赖配置下回放后再决策。"""

    def test_plan_uses_replayed_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不需成交时给出回放得到的当前持仓，并带回放起点与原因。"""
        from quant_etf_api.domain.strategies.risk_overlay import DayTarget
        from quant_etf_api.services import strategy_decision_service as module
        from quant_etf_api.services.backtest_service import LoopPathState

        state = LoopPathState(
            composition_base={"A": 0.6},
            overlay_state=RiskOverlayState(stop_days_left=3),
            last_target={"A": 0.5},
            timing_state=None,
            last_date=_DAY,
        )
        fake_bt = mock.MagicMock()
        fake_bt.replay_path_state.return_value = state
        fake_bt.decide_with_path_state.return_value = DayTarget(
            positions={"A": 0.2}, should_rebalance=False, reason="band_skip"
        )
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.BacktestService",
            mock.MagicMock(return_value=fake_bt),
        )
        svc = module.StrategyDecisionService(mock.MagicMock())
        svc._db.get.return_value = None
        context = _context(current_holdings=None)
        svc.run = mock.MagicMock(return_value=SimpleNamespace(positions={"A": 0.2}))
        _result, plan = svc._decide_with_replay("s", mock.MagicMock(), context)
        assert context.current_holdings == {"A": 0.6}
        assert plan["positions"] == {"A": 0.6}
        assert plan["path_state"]["rebalance_due"] is False
        assert plan["path_state"]["reason"] == "band_skip"
        assert plan["path_state"]["drawdown_stop_days_left"] == 3
        assert plan["path_state"]["replay_start"] == (_DAY - timedelta(days=365)).isoformat()
