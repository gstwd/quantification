"""阶段 4A 配置表达力：嵌套规则组、动态档位、步长限制、条件仓位上限、线性映射。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from pydantic import ValidationError

from quant_etf_api.domain.strategies.models import TimingSignal
from quant_etf_api.domain.strategies.timing_state import TimingState, resolve_tier_exposure
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import (
    FilterConfig,
    FilterRule,
    PortfolioConfig,
    ScoreConfig,
    StrategyConfig,
    TimingConfig,
    TimingExposureCapConfig,
    TimingStateConfig,
    TimingTierConfig,
    filter_rule_refs,
)
from quant_etf_api.engine.filter import DefaultFilterEngine, evaluate_rule_group
from quant_etf_api.engine.portfolio import resolve_target_exposure
from quant_etf_api.engine.score import DefaultScoreCalculator
from quant_etf_api.engine.timing import evaluate_timing
from quant_etf_api.engine.transforms import get_transform, is_valid_transform
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.services.strategy_config_service import StrategyConfigService

TRADE_DATE = date(2025, 1, 15)


def _rule(factor: str, op: str, value: float | list[float]) -> FilterRule:
    """构造单条规则。"""
    return FilterRule(factor=factor, op=op, value=value)


# "(rsi ≤ 25 且 乖离 ≤ −10) 或 乖离 ≤ −15"
_PANIC_OR_DEEP = FilterConfig(
    logic="OR",
    rules=[_rule("ma_deviation", "lte", -15)],
    groups=[
        FilterConfig(
            logic="AND", rules=[_rule("rsi", "lte", 25), _rule("ma_deviation", "lte", -10)]
        )
    ],
)


class TestNestedRuleGroups:
    """嵌套规则组。"""

    @pytest.mark.parametrize(
        ("rsi", "dev", "expected"),
        [(20, -12, True), (40, -12, False), (40, -16, True), (20, -5, False)],
    )
    def test_or_of_and(self, rsi: float, dev: float, expected: bool) -> None:
        """(A 且 B) 或 C 按预期求值。"""
        values = {"rsi": rsi, "ma_deviation": dev}
        assert evaluate_rule_group(_PANIC_OR_DEEP, values.get) is expected

    def test_depth_limited(self) -> None:
        """超过 3 层嵌套被拒绝。"""
        leaf = {"rules": [{"factor": "a", "op": "gt", "value": 0}]}
        with pytest.raises(ValidationError, match="最多嵌套"):
            FilterConfig(groups=[{"groups": [{"groups": [leaf]}]}])

    def test_refs_include_nested(self) -> None:
        """引用收集递归进入子组。"""
        assert filter_rule_refs(_PANIC_OR_DEEP) == ["ma_deviation", "rsi", "ma_deviation"]

    def test_asset_filter_uses_groups_and_reports_path(self) -> None:
        """资产过滤支持子组，明细带子组路径。"""
        context = EngineContext(
            trade_date=TRADE_DATE,
            universe=["A", "B"],
            asset_factors={
                ("A", "rsi"): 20.0,
                ("A", "ma_deviation"): -12.0,
                ("B", "rsi"): 50.0,
                ("B", "ma_deviation"): -12.0,
            },
        )
        debug: list[Any] = []
        result = DefaultFilterEngine().filter(_PANIC_OR_DEEP, {"A": 1.0, "B": 2.0}, context, debug)
        assert result == {"A": 1.0}
        detail_a = next(item for item in debug if item.index_code == "A")
        assert any(r.group_path == "0" for r in detail_a.rule_results)

    def test_flat_filter_unchanged(self) -> None:
        """无子组时按平铺规则求值（AND 短路）。"""
        flat = FilterConfig(rules=[_rule("x", "gt", 0), _rule("y", "gt", 0)])
        context = EngineContext(
            trade_date=TRADE_DATE, universe=["A"], asset_factors={("A", "x"): -1.0}
        )
        debug: list[Any] = []
        assert DefaultFilterEngine().filter(flat, {"A": 1.0}, context, debug) == {}
        assert len(debug[0].rule_results) == 1


class TestDynamicTierExposure:
    """档位维持 / 增减与步长限制（纯函数）。"""

    _BASE: dict[str, Any] = {
        "source": "matched",
        "exposure": None,
        "hold": False,
        "exposure_delta": None,
        "min_exposure": 0.0,
        "max_exposure": 1.0,
        "previous_exposure": 0.5,
        "base_exposure": 0.5,
        "max_step": None,
    }

    @pytest.mark.parametrize(
        ("override", "expected"),
        [
            ({"exposure": 1.0}, 1.0),
            ({"hold": True, "previous_exposure": 0.75}, 0.75),
            ({"hold": True, "previous_exposure": None}, 0.5),
            ({"exposure_delta": 0.25, "previous_exposure": 0.75}, 1.0),
            ({"exposure_delta": 0.25, "previous_exposure": 1.0}, 1.0),
            ({"exposure_delta": -0.25, "min_exposure": 0.25, "previous_exposure": 0.25}, 0.25),
            ({"exposure_delta": 0.25, "source": "held", "previous_exposure": 0.5}, 0.5),
            ({"exposure": 1.0, "max_step": 0.25, "previous_exposure": 0.25}, 0.5),
            ({"exposure": 0.0, "max_step": 0.25, "previous_exposure": None}, 0.0),
        ],
    )
    def test_resolve(self, override: dict[str, Any], expected: float) -> None:
        """固定 / 维持 / 增减 / 沿用 / 步长限制。"""
        assert resolve_tier_exposure(**{**self._BASE, **override}) == expected

    def test_exactly_one_mode(self) -> None:
        """仓位方式三选一。"""
        with pytest.raises(ValidationError, match="之一"):
            TimingTierConfig(regime="neutral", rules=[_rule("a", "gt", 0)])
        with pytest.raises(ValidationError, match="之一"):
            TimingTierConfig(exposure=0.5, hold=True, rules=[_rule("a", "gt", 0)])

    def test_dynamic_tiers_need_base(self) -> None:
        """动态档位必须给起点仓位。"""
        with pytest.raises(ValidationError, match="tier_base_exposure"):
            TimingConfig(
                combine="rules",
                tiers=[TimingTierConfig(hold=True, regime="neutral", rules=[_rule("a", "gt", 0)])],
            )


def _left_side_timing(**extra: Any) -> TimingConfig:
    """左侧 5 档手册：深跌满仓、灰区维持、中枢 50%、涨多 25%、过热 0%；恐慌日 +25%。"""
    return TimingConfig(
        combine="rules",
        tier_base_exposure=0.5,
        tiers=[
            TimingTierConfig(
                label="深跌", exposure=1.0, regime="offensive", rules=[_rule("dev", "lte", -20)]
            ),
            TimingTierConfig(
                label="恐慌",
                exposure_delta=0.25,
                regime="offensive",
                logic="AND",
                rules=[_rule("rsi", "lte", 25), _rule("dev", "lte", -10)],
            ),
            TimingTierConfig(
                label="灰区下",
                hold=True,
                regime="neutral",
                rules=[_rule("dev", "between", [-15, -5])],
            ),
            TimingTierConfig(
                label="中枢",
                exposure=0.5,
                regime="neutral",
                rules=[_rule("dev", "between", [-5, 5])],
            ),
            TimingTierConfig(
                label="灰区上",
                hold=True,
                regime="neutral",
                rules=[_rule("dev", "between", [5, 15])],
            ),
            TimingTierConfig(
                label="过热", exposure=0.0, regime="defensive", rules=[_rule("dev", "gt", 15)]
            ),
        ],
        **extra,
    )


def _run(timing: TimingConfig, days: list[dict[str, float]]) -> list[TimingSignal]:
    """按日推进择时状态层，返回逐日信号。"""
    calculator = DefaultScoreCalculator()
    state: TimingState | None = None
    signals: list[TimingSignal] = []
    for values in days:
        context = EngineContext(
            trade_date=TRADE_DATE, universe=[], market_factors=values, timing_state=state
        )
        signal, state = evaluate_timing(calculator, timing, context)
        signals.append(signal)
    return signals


class TestLeftSideManual:
    """把人工左侧手册写成配置后的逐日仓位。"""

    def test_grey_zone_holds_and_centre_resets(self) -> None:
        """深跌满仓 → 灰区维持满仓 → 回到中枢 50% → 灰区维持 50%。"""
        signals = _run(
            _left_side_timing(),
            [
                {"dev": -22, "rsi": 30},
                {"dev": -12, "rsi": 40},
                {"dev": 0, "rsi": 50},
                {"dev": 8, "rsi": 60},
            ],
        )
        assert [s.exposure for s in signals] == [1.0, 1.0, 0.5, 0.5]
        assert signals[1].factors["tier"]["mode"] == "维持"

    def test_panic_day_adds_one_step(self) -> None:
        """恐慌日在当前仓位上加一档（夹在上限内）。"""
        signals = _run(
            _left_side_timing(),
            [{"dev": 0, "rsi": 50}, {"dev": -12, "rsi": 20}, {"dev": -12, "rsi": 20}],
        )
        assert [s.exposure for s in signals] == [0.5, 0.75, 1.0]

    def test_step_limit(self) -> None:
        """每次最多变动一档：过热时从满仓逐档降。"""
        timing = _left_side_timing(state=TimingStateConfig(max_exposure_step=0.25))
        signals = _run(
            timing,
            [{"dev": -22, "rsi": 30}, {"dev": 20, "rsi": 80}, {"dev": 20, "rsi": 80}],
        )
        assert [s.exposure for s in signals] == [1.0, 0.75, 0.5]

    def test_needs_history(self) -> None:
        """动态档位与步长限制都需要实时回放历史。"""
        assert _left_side_timing().needs_history is True


class TestExposureCaps:
    """条件仓位上限。"""

    def test_cap_applies_minimum_of_matched(self) -> None:
        """命中多条上限取最小值，在档位仓位之后施加。"""
        timing = _left_side_timing(
            exposure_caps=[
                TimingExposureCapConfig(
                    label="估值高", max_exposure=0.5, rules=[_rule("pe_pct", "gte", 80)]
                ),
                TimingExposureCapConfig(
                    label="估值极高", max_exposure=0.25, rules=[_rule("pe_pct", "gte", 90)]
                ),
            ]
        )
        signals = _run(timing, [{"dev": -22, "rsi": 30, "pe_pct": 85}])
        assert signals[0].exposure == 1.0
        assert (signals[0].exposure_cap, signals[0].exposure_cap_label) == (0.5, "估值高")
        portfolio = PortfolioConfig(method="equal_weight")
        assert resolve_target_exposure(portfolio, signals[0]) == 0.5
        assert timing.market_refs()[-1] == "pe_pct"

    def test_cap_also_limits_regime_table(self) -> None:
        """未命中档位、按 regime 仓位表时上限同样生效。"""
        signal = TimingSignal(regime="offensive", confidence=60, label="进攻", exposure_cap=0.3)
        assert resolve_target_exposure(PortfolioConfig(method="equal_weight"), signal) == 0.3


class TestLinearTransform:
    """参数化线性映射。"""

    def test_mapping_and_validity(self) -> None:
        """[lo, hi] → [0, 100] 截断；lo > hi 方向反转；lo == hi 非法。"""
        f = get_transform("linear(-20, 20)")
        assert (f(-25), f(0), f(20)) == (0.0, 50.0, 100.0)
        assert get_transform("linear(20,-20)")(10) == 25.0
        assert is_valid_transform("linear(1,1)") is False
        assert is_valid_transform("linear(a,b)") is False
        with pytest.raises(KeyError):
            get_transform("linear(1,1)")


class _FakeRow(SimpleNamespace):
    """因子定义行替身。"""

    def __init__(self, factor_id: str) -> None:
        super().__init__(factor_id=factor_id, is_active=True)


def _validate(timing: TimingConfig) -> tuple[list[str], list[str]]:
    """校验只含择时块的策略配置。"""
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        _FakeRow(fid) for fid in build_default_registry().ids()
    ]
    config = StrategyConfig(
        strategy_id="v",
        display_name="v",
        score=ScoreConfig(factors={"pe_percentile": 1.0}),
        timing=timing,
    )
    result = StrategyConfigService(db=db).validate_config(config.model_dump())
    return result.errors, result.warnings


class TestValidation:
    """服务层校验。"""

    def test_empty_nested_group_rejected(self) -> None:
        """空子组报错。"""
        timing = TimingConfig(
            combine="rules",
            tiers=[
                TimingTierConfig(
                    exposure=1.0,
                    regime="offensive",
                    rules=[_rule("rsi", "lt", 30)],
                    groups=[FilterConfig()],
                )
            ],
        )
        errors, _ = _validate(timing)
        assert any("空规则组" in e for e in errors)

    def test_cap_requires_rules(self) -> None:
        """条件上限至少一条规则。"""
        timing = TimingConfig(
            factors={"rsi": 1.0},
            exposure_caps=[TimingExposureCapConfig(max_exposure=0.5)],
        )
        errors, _ = _validate(timing)
        assert any("exposure_caps[0]" in e for e in errors)

    def test_linear_transform_accepted(self) -> None:
        """linear(lo,hi) 通过变换校验。"""
        errors, _ = _validate(
            TimingConfig(
                factors={"ma_deviation": 1.0}, transforms={"ma_deviation": "linear(-20,20)"}
            )
        )
        assert not any("变换" in e for e in errors)

    def test_step_without_tiers_warns(self) -> None:
        """没有档位时步长限制无效，给出警告。"""
        _, warnings = _validate(
            TimingConfig(factors={"rsi": 1.0}, state=TimingStateConfig(max_exposure_step=0.25))
        )
        assert any("max_exposure_step" in w for w in warnings)
