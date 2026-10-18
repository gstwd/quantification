"""择时多档仓位（timing.tiers）的回归测试。

覆盖：
- 默认口径不变：未配置档位时信号、明细、目标仓位与择时统计和改造前一致；
- 加权模式：综合分分段映射仓位的边界（≥ 下限命中、低于全部下限回退 regime 仓位表）；
- 规则模式：按顺序首个命中、灰区未命中回退中性、逐档明细、全部缺失降级；
- 状态层：沿用前 regime 时沿用上一档位、同 regime 内换档当日生效、降级回退；
- 组合：档位仓位优先于 timing_exposure（含 regime_rules 覆盖），再经组合级风控裁剪；
- 观测：择时统计按档位分组、回测主循环逐日传递档位、实时回放重建档位；
- 配置校验：两种模式下档位的错误与警告。
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from quant_etf_api.domain.research.timing import TimingDay, compute_timing_stats
from quant_etf_api.domain.strategies.models import TimingSignal
from quant_etf_api.domain.strategies.timing_proxy import TimingProxySelection
from quant_etf_api.domain.strategies.timing_state import TimingState, resolve_effective_tier
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import (
    FilterConfig,
    FilterRule,
    PortfolioConfig,
    RankConfig,
    RegimeRuleConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
    TimingConfig,
    TimingRulesConfig,
    TimingStateConfig,
    TimingThresholds,
    TimingTierConfig,
)
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.engine.portfolio import resolve_target_exposure
from quant_etf_api.engine.score import DefaultScoreCalculator, match_score_tier
from quant_etf_api.engine.timing import evaluate_timing
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.factors.compute import FactorMatrix
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService, _parse_timing_stats
from quant_etf_api.services.strategy_config_service import StrategyConfigService

TRADE_DATE = date(2025, 1, 15)


def _context(
    market_factors: dict[str, float | None],
    timing_state: TimingState | None = None,
    previous_timing_regime: str | None = None,
) -> EngineContext:
    """构造只含择时因子的引擎上下文。"""
    return EngineContext(
        trade_date=TRADE_DATE,
        universe=[],
        market_factors=market_factors,
        timing_state=timing_state,
        previous_timing_regime=previous_timing_regime,
    )


def _rule(factor: str, op: str, value: float | list[float]) -> FilterRule:
    """构造单条规则。"""
    return FilterRule(factor=factor, op=op, value=value)


# 加权模式 5 档：综合分 ≥80 满仓 … <35 空仓
_SCORE_LADDER = [
    TimingTierConfig(label="很便宜", min_score=80, exposure=1.0),
    TimingTierConfig(label="便宜", min_score=65, exposure=0.75),
    TimingTierConfig(label="中枢", min_score=50, exposure=0.5),
    TimingTierConfig(label="偏贵", min_score=35, exposure=0.25),
    TimingTierConfig(label="很贵", min_score=0, exposure=0.0),
]


def _weighted(tiers: list[TimingTierConfig] | None = None, **overrides: Any) -> TimingConfig:
    """单因子（原值即 0-100）加权择时：综合分等于因子原值。"""
    return TimingConfig(
        factors={"t": 1.0},
        thresholds=TimingThresholds(offensive=65, defensive=35),
        tiers=list(_SCORE_LADDER if tiers is None else tiers),
        **overrides,
    )


# 规则模式 5 档：中证1000 年线乖离的人工规则（灰区 −15~−5、+5~+15 不命中）
_DEV_TIERS = [
    TimingTierConfig(
        label="深度超跌", regime="offensive", exposure=1.0, rules=[_rule("dev", "lte", -20)]
    ),
    TimingTierConfig(
        label="超跌", regime="offensive", exposure=0.75, rules=[_rule("dev", "lte", -15)]
    ),
    TimingTierConfig(
        label="过热", regime="defensive", exposure=0.0, rules=[_rule("dev", "gte", 25)]
    ),
    TimingTierConfig(
        label="涨多", regime="defensive", exposure=0.25, rules=[_rule("dev", "gte", 15)]
    ),
    TimingTierConfig(
        label="中枢", regime="neutral", exposure=0.5, rules=[_rule("dev", "between", [-5, 5])]
    ),
]


def _rules(tiers: list[TimingTierConfig] | None = None, **overrides: Any) -> TimingConfig:
    """规则模式多档仓位择时。"""
    return TimingConfig(
        combine="rules", tiers=list(_DEV_TIERS if tiers is None else tiers), **overrides
    )


def _evaluate(
    timing: TimingConfig, value: float | None, state: TimingState | None = None, ref: str = ""
) -> tuple[TimingSignal, TimingState]:
    """对单个择时因子取值评估一次择时。"""
    factor = ref or ("t" if timing.combine == "weighted" else "dev")
    return evaluate_timing(
        DefaultScoreCalculator(), timing, _context({factor: value}, timing_state=state)
    )


def _run_sequence(timing: TimingConfig, values: list[float | None]) -> list[TimingSignal]:
    """按交易日顺序逐日推进状态，返回逐日有效信号。"""
    state: TimingState | None = None
    signals: list[TimingSignal] = []
    for value in values:
        signal, state = _evaluate(timing, value, state)
        signals.append(signal)
    return signals


# ── 1. 默认口径不变 ──────────────────────────────────────────────────────


class TestDefaultUnchanged:
    """未配置档位时与改造前完全一致。"""

    @pytest.mark.parametrize(
        "timing",
        [
            TimingConfig(factors={"t": 1.0}),
            TimingConfig(
                combine="rules",
                rules=TimingRulesConfig(
                    offensive=FilterConfig(rules=[_rule("t", "gt", 60)]),
                    defensive=FilterConfig(rules=[_rule("t", "lt", 40)]),
                ),
            ),
        ],
        ids=["weighted", "rules"],
    )
    @pytest.mark.parametrize("value", [10.0, 50.0, 90.0])
    def test_signal_has_no_tier(self, timing: TimingConfig, value: float) -> None:
        """信号不带档位、明细不出现档位键，目标仓位按 regime 查表。"""
        assert timing.tiers == []
        signal, state = evaluate_timing(DefaultScoreCalculator(), timing, _context({"t": value}))
        assert (signal.tier, signal.tier_label, signal.exposure) == (None, None, None)
        assert state.tier is None
        assert not {"tier", "matched_tier", "tier_rules"} & set(signal.factors)
        portfolio = PortfolioConfig(
            method="equal_weight",
            timing_exposure={"offensive": 0.9, "neutral": 0.6, "defensive": 0.1},
        )
        expected = portfolio.timing_exposure[signal.regime]  # type: ignore[index]
        assert resolve_target_exposure(portfolio, signal) == expected

    def test_default_table_without_timing_exposure(self) -> None:
        """未配置 timing_exposure 时仍按默认三档表。"""
        portfolio = PortfolioConfig(method="equal_weight")
        signal = TimingSignal(regime="defensive", confidence=60.0, label="防守")
        assert resolve_target_exposure(portfolio, signal) == 0.20
        assert resolve_target_exposure(portfolio, None) == portfolio.default_exposure

    def test_engine_payload_unchanged(self) -> None:
        """引擎逐资产 payload 不出现档位字段。"""
        result = StrategyEngine().run(
            _strategy(TimingConfig(factors={"t": 1.0})), _engine_context(80.0)
        )
        payload = result.strategy_results[0].payload
        assert "timing_tier_label" not in payload
        assert "档位" not in payload["plan_reasoning"]

    def test_timing_stats_tiers_none(self) -> None:
        """未启用档位时择时统计的 tiers 为 None。"""
        stats = compute_timing_stats(
            [TimingDay(regime="neutral", score=50.0, degraded=False, portfolio_return=0.1)]
        )
        assert stats is not None
        assert stats.tiers is None
        assert stats.to_dict()["tiers"] is None


# ── 2. 加权模式：综合分分段 ──────────────────────────────────────────────


class TestWeightedTiers:
    """综合分 ≥ 档位下限即命中，按顺序首个命中。"""

    @pytest.mark.parametrize(
        ("score", "tier", "exposure", "regime"),
        [
            (100.0, 0, 1.0, "offensive"),
            (80.0, 0, 1.0, "offensive"),
            (79.99, 1, 0.75, "offensive"),
            (65.0, 1, 0.75, "offensive"),
            (64.99, 2, 0.5, "neutral"),
            (50.0, 2, 0.5, "neutral"),
            (49.99, 3, 0.25, "neutral"),
            (35.0, 3, 0.25, "defensive"),
            (34.99, 4, 0.0, "defensive"),
            (0.0, 4, 0.0, "defensive"),
        ],
    )
    def test_boundaries(self, score: float, tier: int, exposure: float, regime: str) -> None:
        """档位边界含下限；regime 仍由阈值独立判定。"""
        signal, state = _evaluate(_weighted(), score)
        assert signal.tier == tier
        assert signal.exposure == exposure
        assert signal.regime == regime
        assert state.tier == tier
        assert signal.factors["tier"]["source"] == "matched"
        assert signal.factors["matched_tier"] == tier

    def test_uses_unrounded_composite(self) -> None:
        """与阈值同用未取整综合分：79.96 展示为 80.0 但不命中 ≥80 档。"""
        assert match_score_tier(_weighted(), 79.96) == 1
        signal, _state = _evaluate(_weighted(), 79.96)
        assert signal.factors["composite_score"] == 80.0
        assert signal.tier == 1

    def test_below_all_tiers_falls_back_to_regime_table(self) -> None:
        """低于全部下限：无档位，按 regime 查 timing_exposure。"""
        timing = _weighted(tiers=_SCORE_LADDER[:3])
        signal, state = _evaluate(timing, 20.0)
        assert (signal.tier, signal.exposure, state.tier) == (None, None, None)
        assert signal.factors["tier"] == {
            "index": None,
            "label": None,
            "exposure": None,
            "source": "none",
            "matched_index": None,
        }
        portfolio = PortfolioConfig(
            method="equal_weight",
            timing_exposure={"offensive": 1.0, "neutral": 0.5, "defensive": 0.3},
        )
        assert resolve_target_exposure(portfolio, signal) == 0.3


# ── 3. 规则模式：按顺序首个命中 ──────────────────────────────────────────


class TestRuleTiers:
    """规则档位决定原始 regime 与仓位。"""

    @pytest.mark.parametrize(
        ("dev", "tier", "exposure", "regime"),
        [
            (-30.0, 0, 1.0, "offensive"),
            (-20.0, 0, 1.0, "offensive"),
            (-19.99, 1, 0.75, "offensive"),
            (-15.0, 1, 0.75, "offensive"),
            (-14.99, None, None, "neutral"),
            (-5.0, 4, 0.5, "neutral"),
            (0.0, 4, 0.5, "neutral"),
            (5.0, 4, 0.5, "neutral"),
            (10.0, None, None, "neutral"),
            (15.0, 3, 0.25, "defensive"),
            (24.99, 3, 0.25, "defensive"),
            (25.0, 2, 0.0, "defensive"),
        ],
    )
    def test_csi1000_five_tiers(
        self, dev: float, tier: int | None, exposure: float | None, regime: str
    ) -> None:
        """中证1000 人工 5 档：灰区未命中按中性回退 regime 仓位表。"""
        signal, _state = _evaluate(_rules(), dev)
        assert (signal.tier, signal.exposure, signal.regime) == (tier, exposure, regime)
        if tier is not None:
            assert signal.tier_label == _DEV_TIERS[tier].label

    def test_order_is_priority(self) -> None:
        """重叠条件按列表顺序：把 ≤−15 档放到前面后 −30 命中它。"""
        signal, _state = _evaluate(_rules(tiers=[_DEV_TIERS[1], _DEV_TIERS[0]]), -30.0)
        assert (signal.tier, signal.exposure) == (0, 0.75)

    def test_detail_lists_every_tier(self) -> None:
        """逐档明细覆盖全部档位，只取第一个命中者；不再输出规则组明细。"""
        signal, _state = _evaluate(_rules(), -25.0)
        tier_rules = signal.factors["tier_rules"]
        assert [item["matched"] for item in tier_rules] == [True, True, False, False, False]
        assert tier_rules[0]["rules"][0]["value"] == -25.0
        assert "rule_groups" not in signal.factors
        assert signal.factors["composite_score"] == 100.0

    def test_compound_condition_via_order(self) -> None:
        """(RSI≤25 且 乖离≤−10) 或 乖离≤−15：拆成两个同仓位档位按顺序匹配。"""
        timing = _rules(
            tiers=[
                TimingTierConfig(
                    regime="offensive",
                    exposure=0.75,
                    rules=[_rule("rsi", "lte", 25), _rule("dev", "lte", -10)],
                ),
                TimingTierConfig(
                    regime="offensive", exposure=0.75, rules=[_rule("dev", "lte", -15)]
                ),
            ]
        )
        calc = DefaultScoreCalculator()
        hit, _s = evaluate_timing(calc, timing, _context({"rsi": 20.0, "dev": -12.0}))
        miss, _s = evaluate_timing(calc, timing, _context({"rsi": 40.0, "dev": -12.0}))
        assert (hit.tier, hit.exposure) == (0, 0.75)
        assert (miss.tier, miss.regime) == (None, "neutral")

    def test_all_missing_degrades_without_tier(self) -> None:
        """全部引用因子缺失：按 on_degraded 回退，不使用档位仓位。"""
        signal, state = _evaluate(_rules(on_degraded="defensive"), None)
        assert signal.factors["degraded"] is True
        assert (signal.regime, signal.tier, signal.exposure, state.tier) == (
            "defensive",
            None,
            None,
            None,
        )

    def test_rule_refs_include_tier_rules(self) -> None:
        """档位规则引用的因子参与因子加载与引用校验。"""
        timing = _rules(
            tiers=[
                _DEV_TIERS[0],
                TimingTierConfig(
                    regime="defensive",
                    exposure=0.0,
                    rules=[FilterRule(factor="a", op="gt", compare_to="b")],
                ),
            ]
        )
        assert timing.factor_refs() == ["dev", "a", "b"]


# ── 4. 状态层与档位 ──────────────────────────────────────────────────────


class TestTierWithState:
    """有效档位跟随有效 regime。"""

    def test_hold_on_neutral_keeps_previous_tier(self) -> None:
        """灰区未命中（中性）+ 无信号维持：沿用上一 regime 与档位仓位。"""
        timing = _rules(state=TimingStateConfig(hold_on_neutral=True))
        first, second = _run_sequence(timing, [-25.0, -10.0])
        assert (first.tier, first.exposure) == (0, 1.0)
        assert (second.regime, second.tier, second.exposure) == ("offensive", 0, 1.0)
        assert second.factors["tier"]["source"] == "held"
        assert second.factors["tier"]["matched_index"] is None

    def test_confirm_days_holds_tier_until_switch(self) -> None:
        """确认期内维持原 regime 与原档位，确认后切到新档位。"""
        timing = _weighted(state=TimingStateConfig(confirm_days=2))
        signals = _run_sequence(timing, [90.0, 20.0, 20.0])
        assert [(s.regime, s.tier, s.exposure) for s in signals] == [
            ("offensive", 0, 1.0),
            ("offensive", 0, 1.0),
            ("defensive", 4, 0.0),
        ]

    def test_hysteresis_holds_tier(self) -> None:
        """滞回带内维持进攻时沿用进攻档位，而不是跳到中性档位。"""
        timing = _weighted(state=TimingStateConfig(hysteresis=10))
        signals = _run_sequence(timing, [70.0, 58.0])
        assert [(s.regime, s.tier) for s in signals] == [("offensive", 1), ("offensive", 1)]

    def test_same_regime_tier_change_is_immediate(self) -> None:
        """同一 regime 内换档不受最短持有约束，当日生效。"""
        timing = _rules(state=TimingStateConfig(min_hold_days=5))
        signals = _run_sequence(timing, [-25.0, -17.0, 20.0])
        assert [(s.regime, s.tier) for s in signals] == [
            ("offensive", 0),
            ("offensive", 1),
            # 切换到防守受最短持有约束：维持进攻并沿用上一档位
            ("offensive", 1),
        ]

    def test_degraded_hold_previous_keeps_tier(self) -> None:
        """降级 hold_previous：regime 与档位都沿用上一决策日。"""
        signals = _run_sequence(_rules(on_degraded="hold_previous"), [-17.0, None])
        assert (signals[1].regime, signals[1].tier, signals[1].exposure) == ("offensive", 1, 0.75)
        assert signals[1].factors["tier"]["source"] == "held"

    def test_degraded_neutral_drops_tier(self) -> None:
        """降级 neutral：回退中性 regime 仓位表，不沿用档位。"""
        signals = _run_sequence(_rules(), [0.0, None])
        assert (signals[1].regime, signals[1].tier, signals[1].exposure) == ("neutral", None, None)

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"regime": "offensive", "raw_regime": "offensive", "raw_tier": 2}, (2, "matched")),
            ({"regime": "neutral", "raw_regime": "neutral", "raw_tier": None}, (None, "none")),
            ({"regime": "offensive", "raw_regime": "neutral", "raw_tier": 3}, (1, "held")),
            (
                {"regime": "defensive", "raw_regime": "neutral", "raw_tier": None},
                (None, "none"),
            ),
        ],
    )
    def test_resolve_effective_tier(
        self, kwargs: dict[str, Any], expected: tuple[int | None, str]
    ) -> None:
        """纯函数：接受当日判定用命中档位，沿用前 regime 用上一档位，否则无档位。"""
        previous = TimingState(regime="offensive", tier=1)
        assert (
            resolve_effective_tier(
                previous=previous, degraded=False, hold_previous_on_degraded=False, **kwargs
            )
            == expected
        )

    def test_out_of_range_tier_from_state_is_ignored(self) -> None:
        """状态中残留的越界档位序号（配置改动后）按无档位处理。"""
        timing = _rules(state=TimingStateConfig(hold_on_neutral=True))
        signal, state = _evaluate(timing, -10.0, TimingState(regime="offensive", tier=9))
        assert (signal.regime, signal.tier, signal.exposure, state.tier) == (
            "offensive",
            None,
            None,
            None,
        )


# ── 5. 组合仓位 ──────────────────────────────────────────────────────────


def _strategy(timing: TimingConfig, **overrides: Any) -> StrategyConfig:
    """单资产赢家通吃策略（便于直接读出总仓位）。"""
    fields: dict[str, Any] = {
        "strategy_id": "tiers",
        "display_name": "tiers",
        "timing": timing,
        "score": ScoreConfig(factors={"val": 1.0}),
        "rank": RankConfig(top_n=1),
        "portfolio": PortfolioConfig(
            method="winner_take_all",
            timing_exposure={"offensive": 0.9, "neutral": 0.6, "defensive": 0.3},
        ),
    }
    fields.update(overrides)
    return StrategyConfig(**fields)


def _engine_context(t: float | None = None, dev: float | None = None) -> EngineContext:
    """单资产上下文：资产因子 val，市场因子 t / dev。"""
    return EngineContext(
        trade_date=TRADE_DATE,
        universe=[{"index_code": "000852", "name_cn": "中证1000", "category": "broad_index"}],
        asset_factors={("000852", "val"): 50.0},
        market_factors={"t": t, "dev": dev},
    )


class TestPortfolioExposure:
    """档位仓位决定总仓位，组合级风控仍然生效。"""

    def test_tier_overrides_timing_exposure(self) -> None:
        """命中档位时取档位仓位；未命中时按 timing_exposure。"""
        engine = StrategyEngine()
        hit = engine.run(_strategy(_rules()), _engine_context(dev=-17.0))
        gray = engine.run(_strategy(_rules()), _engine_context(dev=-10.0))
        assert hit.total_exposure == 0.75 and hit.timing_target_exposure == 0.75
        assert gray.total_exposure == 0.6 and gray.timing_target_exposure == 0.6
        payload = hit.strategy_results[0].payload
        assert payload["timing_tier_label"] == "超跌"
        assert payload["timing_tier_exposure"] == 0.75
        assert "档位仓位 75%" in payload["plan_reasoning"]

    def test_tier_overrides_regime_rules_portfolio(self) -> None:
        """档位仓位也优先于 regime_rules 覆盖的 timing_exposure。"""
        config = _strategy(
            _rules(),
            regime_rules={
                "offensive": RegimeRuleConfig(
                    portfolio=PortfolioConfig(
                        method="winner_take_all", timing_exposure={"offensive": 0.4}
                    )
                )
            },
        )
        result = StrategyEngine().run(config, _engine_context(dev=-25.0))
        assert result.total_exposure == 1.0

    def test_risk_cap_still_applies(self) -> None:
        """组合级风控上限裁剪档位仓位。"""
        config = _strategy(
            _rules(), risk=RiskConfig(max_asset_weight=1.0, max_portfolio_exposure=0.8)
        )
        result = StrategyEngine().run(config, _engine_context(dev=-25.0))
        assert result.total_exposure == 0.8
        assert result.timing_target_exposure == 0.8

    def test_zero_exposure_tier_is_empty_book(self) -> None:
        """0% 档位：总仓位为 0，而不是回退 regime 仓位表。"""
        result = StrategyEngine().run(_strategy(_rules()), _engine_context(dev=30.0))
        assert result.timing is not None and result.timing.exposure == 0.0
        assert result.total_exposure == 0.0
        assert result.timing_target_exposure == 0.0


# ── 6. 择时统计 ──────────────────────────────────────────────────────────


class TestTierStats:
    """择时统计按有效档位分组。"""

    def test_groups_by_tier_with_unmatched_bucket(self) -> None:
        """每个档位一项（未出现的 days=0），末尾为未命中档位组。"""
        days = [
            TimingDay(
                regime="offensive",
                score=100.0,
                degraded=False,
                portfolio_return=1.0,
                benchmark_return=2.0,
                exposure=1.0,
                tier=0,
            ),
            TimingDay(
                regime="offensive",
                score=100.0,
                degraded=False,
                portfolio_return=0.5,
                benchmark_return=1.0,
                exposure=1.0,
                tier=0,
            ),
            TimingDay(
                regime="neutral",
                score=50.0,
                degraded=False,
                portfolio_return=-0.2,
                benchmark_return=-0.4,
                exposure=0.5,
                tier=None,
            ),
            TimingDay(
                regime="defensive",
                score=0.0,
                degraded=False,
                portfolio_return=0.0,
                benchmark_return=-1.0,
                exposure=0.25,
                tier=2,
            ),
        ]
        stats = compute_timing_stats(
            days, tiers=[("深度超跌", 1.0), ("超跌", 0.75), ("涨多", 0.25)]
        )
        assert stats is not None and stats.tiers is not None
        assert [(t.tier, t.label, t.exposure, t.days) for t in stats.tiers] == [
            (0, "深度超跌", 1.0, 2),
            (1, "超跌", 0.75, 0),
            (2, "涨多", 0.25, 1),
            (None, "未命中档位（按 regime 仓位）", None, 1),
        ]
        first = stats.tiers[0]
        assert first.share == 0.5
        assert first.average_exposure == 1.0
        assert first.mean_daily_return_pct == pytest.approx(0.75)
        assert first.benchmark_mean_daily_return_pct == pytest.approx(1.5)
        # regime 分组口径不受影响
        assert [r.days for r in stats.regimes] == [2, 1, 1]


# ── 7. 回测主循环与实时回放 ──────────────────────────────────────────────

_LOOP_DATES = [
    d for d in (date(2025, 1, 6) + timedelta(days=i) for i in range(12)) if d.weekday() < 5
][:6]


def _loop_service(dev_values: list[float | None]) -> BacktestService:
    """真实上下文构建 + 真实引擎的内存回测服务（单资产、择时因子 dev）。"""
    svc = BacktestService(db=mock.MagicMock())
    svc._ensure_market_scope_bars = mock.MagicMock()
    svc._get_lookback_days = mock.MagicMock(return_value=90)
    svc._write_index_results = mock.MagicMock(return_value=(0, 0))
    bars = {
        ("000852", d): SimpleNamespace(
            close_price=100.0, open_price=100.0, high_price=100.0, low_price=100.0
        )
        for d in _LOOP_DATES
    }
    universe = [{"index_code": "000852", "name_cn": "000852", "category": "broad_index"}]
    svc._prepare_backtest_data = mock.MagicMock(
        return_value=(universe, ["000852"], list(_LOOP_DATES), bars, {}, {})
    )
    values = {
        d: {("000852", "val"): 1.0, ("000852", "dev"): dev}
        for d, dev in zip(_LOOP_DATES, dev_values)
    }
    svc._factor_provider = mock.MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = FactorMatrix(values=values)
    svc._context_builder = ContextBuilder(mock.MagicMock(), factor_provider=svc._factor_provider)
    svc._engine = StrategyEngine()
    svc._backtest_repo = mock.MagicMock()
    return svc


def _loop_row() -> BacktestRunModel:
    """构造运行中的回测行。"""
    return BacktestRunModel(
        backtest_id="bt-tiers",
        strategy_id="tiers",
        start_date=_LOOP_DATES[0],
        end_date=_LOOP_DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["000852"]},
        params={"_execution_model": "t_plus_1_open", "_data_quality_mode": "warn"},
        status="running",
    )


class TestBacktestLoopTiers:
    """回测主循环逐日传递档位并写入分档统计。"""

    def test_loop_tiers_and_stats(self) -> None:
        """逐日目标仓位随档位变化，灰区沿用（hold_on_neutral），统计按档位分组。"""
        timing = _rules(proxy_index_codes=["000852"], state=TimingStateConfig(hold_on_neutral=True))
        config = _strategy(timing, index_codes=["000852"], risk=RiskConfig(max_asset_weight=1.0))
        svc = _loop_service([-25.0, -10.0, -17.0, 20.0, 30.0, 0.0])
        outcome = svc._run_backtest_loop("bt-tiers", _loop_row(), config)

        days = outcome["timing_days"]
        assert [d.tier for d in days] == [0, 0, 1, 3, 2]
        assert [d.target_exposure for d in days] == [1.0, 1.0, 0.75, 0.25, 0.0]
        stats = _parse_timing_stats(outcome["metrics"])
        assert stats is not None and stats.tiers is not None
        assert [t.days for t in stats.tiers] == [2, 1, 1, 1, 0, 0]


class TestLiveReplayTier:
    """实时路径回放重建的状态带有效档位。"""

    def test_replay_restores_tier(self) -> None:
        """回放最近交易日后，上一决策日的档位随状态一起还原（只读）。"""
        days = [date(2025, 1, 6) + timedelta(days=i) for i in range(3)]
        provider = mock.MagicMock()
        provider.load_market_factor_history.return_value = {
            day: TimingProxySelection(
                values={"dev": value}, factor_proxies={"dev": "000852"}, proxy_index_code="000852"
            )
            for day, value in zip(days, [-25.0, -17.0, -10.0])
        }
        builder = ContextBuilder(mock.MagicMock(), factor_provider=provider)
        config = _strategy(_rules(state=TimingStateConfig(hold_on_neutral=True)))
        with mock.patch(
            "quant_etf_api.infra.db.repositories.index_daily_bar."
            "IndexDailyBarRepository.find_trading_dates",
            return_value=[*days, TRADE_DATE],
        ):
            state = builder._replay_timing_state(config, TRADE_DATE)
        assert state is not None
        assert (state.regime, state.tier) == ("offensive", 1)


# ── 8. 配置校验 ──────────────────────────────────────────────────────────


class _FakeRow:
    """极简 factor_definition 行替身，仅暴露 factor_id。"""

    def __init__(self, factor_id: str) -> None:
        self.factor_id = factor_id


def _validate(timing: TimingConfig, **overrides: Any) -> tuple[list[str], list[str]]:
    """校验只改动择时块（及可选覆盖）的策略配置。"""
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        _FakeRow(fid) for fid in build_default_registry().ids()
    ]
    fields: dict[str, Any] = {
        "strategy_id": "v",
        "display_name": "v",
        "score": ScoreConfig(factors={"pe_percentile": 1.0}),
        "timing": timing,
    }
    fields.update(overrides)
    result = StrategyConfigService(db=db).validate_config(StrategyConfig(**fields).model_dump())
    return result.errors, result.warnings


def _rsi_tier(**kwargs: Any) -> TimingTierConfig:
    """引用 rsi 的规则档位。"""
    return TimingTierConfig(
        exposure=kwargs.pop("exposure", 0.5), rules=[_rule("rsi", "lt", 30)], **kwargs
    )


class TestTierValidation:
    """档位在两种模式下的校验。"""

    def test_valid_configs_pass(self) -> None:
        """合法的加权 / 规则档位不报错。"""
        weighted = TimingConfig(factors={"rsi": 1.0}, tiers=_SCORE_LADDER)
        rules = TimingConfig(
            combine="rules",
            tiers=[_rsi_tier(regime="offensive", exposure=1.0), _rsi_tier(regime="neutral")],
        )
        for timing in (weighted, rules):
            errors, _w = _validate(timing)
            assert errors == []

    def test_weighted_requires_descending_min_score(self) -> None:
        """加权档位缺下限或未严格降序时报错。"""
        errors, _w = _validate(
            TimingConfig(
                factors={"rsi": 1.0},
                tiers=[
                    TimingTierConfig(min_score=50, exposure=0.5),
                    TimingTierConfig(min_score=50, exposure=0.2),
                    TimingTierConfig(exposure=0.1),
                ],
            )
        )
        assert any("timing.tiers[1].min_score=50 必须小于上一档" in e for e in errors), errors
        assert any("timing.tiers[2].min_score 必填" in e for e in errors), errors

    def test_weighted_rules_and_gap_warning(self) -> None:
        """加权档位不能带规则；regime 被忽略与未覆盖区间给出警告。"""
        errors, warnings = _validate(
            TimingConfig(
                factors={"rsi": 1.0},
                tiers=[_rsi_tier(min_score=60, regime="offensive")],
            )
        )
        assert any("不能配置 rules" in e for e in errors)
        assert any("timing.tiers[0].regime 在加权模式下被忽略" in w for w in warnings)
        assert any("综合分低于 60 时未命中任何档位" in w for w in warnings)

    def test_rules_tier_requires_regime_and_rules(self) -> None:
        """规则档位必须有 regime 与至少一条规则；规则跨字段校验带档位路径。"""
        errors, _w = _validate(
            TimingConfig(
                combine="rules",
                tiers=[
                    TimingTierConfig(exposure=0.5),
                    TimingTierConfig(
                        regime="offensive", exposure=1.0, rules=[FilterRule(factor="rsi", op="gt")]
                    ),
                ],
            )
        )
        assert any("timing.tiers[0].regime 必填" in e for e in errors), errors
        assert any("timing.tiers[0] 至少需要一条命中规则" in e for e in errors), errors
        assert any("timing.tiers[1].rules[0]" in e for e in errors), errors

    def test_rules_tiers_exclusive_with_groups(self) -> None:
        """规则模式下档位与进攻 / 防守规则组互斥；只配档位时不再要求规则组。"""
        errors, _w = _validate(
            TimingConfig(
                combine="rules",
                rules=TimingRulesConfig(offensive=FilterConfig(rules=[_rule("rsi", "gt", 70)])),
                tiers=[_rsi_tier(regime="defensive")],
            )
        )
        assert any("互斥" in e for e in errors), errors
        errors, _w = _validate(TimingConfig(combine="rules", tiers=[_rsi_tier(regime="defensive")]))
        assert not any("至少配置一条规则" in e for e in errors), errors

    def test_hold_on_neutral_with_neutral_tier_warns(self) -> None:
        """无信号维持会吞掉中性档位，需提示。"""
        _e, warnings = _validate(
            TimingConfig(
                combine="rules",
                tiers=[_rsi_tier(regime="neutral")],
                state=TimingStateConfig(hold_on_neutral=True),
            )
        )
        assert any("hold_on_neutral 对 neutral 档位同样生效" in w for w in warnings)

    def test_portfolio_warnings_consider_tiers(self) -> None:
        """赢家通吃单资产上限纳入档位仓位；regime_rules 的 timing_exposure 提示优先级。"""
        _e, warnings = _validate(
            TimingConfig(combine="rules", tiers=[_rsi_tier(regime="offensive", exposure=1.0)]),
            portfolio=PortfolioConfig(
                method="winner_take_all",
                timing_exposure={"offensive": 0.5, "neutral": 0.5, "defensive": 0.5},
            ),
            risk=RiskConfig(max_asset_weight=0.6),
            regime_rules={
                "offensive": RegimeRuleConfig(
                    portfolio=PortfolioConfig(
                        method="winner_take_all", timing_exposure={"offensive": 0.5}
                    )
                )
            },
        )
        assert any("winner_take_all 的目标仓位最高 100%" in w for w in warnings), warnings
        assert any("只在未命中仓位档位时生效" in w for w in warnings), warnings
