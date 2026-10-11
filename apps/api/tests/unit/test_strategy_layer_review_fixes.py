"""策略层审查问题修复的回归测试。

覆盖：
- 排名只按综合得分：sort_by 非 score、枚举写错（order/logic 等）在解析期被拒绝；
- regime 覆盖块与顶层同等校验，及 regime_rules 无择时、赢家通吃被裁剪的提示；
- absolute 模式 zero 缺失策略"按最差处理"（不能先置 0 再变换）；
- 引擎在评分前统一剔除决策必需因子缺失的资产（regime 感知、尊重各模块缺失语义）；
- 择时目标仓位与当日选股结果无关；择时因子全部缺失时标记降级；
- 排名并列按代码决定先后、估值子排名按原值升序；
- 星标摘要 plan_mode、回测快照损坏不回退、实时缺失告警覆盖择时因子。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest import mock

import pytest
from pydantic import ValidationError

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
    TimingThresholds,
)
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.services.backtest_diagnostics import collect_compute_failure_warnings
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import StrategyConfigService
from quant_etf_api.services.strategy_decision_service import _plan_mode

TRADE_DATE = date(2025, 1, 15)
CODES = ["A", "B", "C", "D"]


def _context(
    asset_factors: dict[tuple[str, str], float | None],
    market_factors: dict[str, float | None] | None = None,
    market_shape_refs: set[str] | None = None,
) -> EngineContext:
    """构造四资产引擎上下文。"""
    return EngineContext(
        trade_date=TRADE_DATE,
        universe=[{"index_code": code, "name_cn": code} for code in CODES],
        asset_factors=asset_factors,
        market_factors=market_factors or {},
        market_shape_refs=market_shape_refs or set(),
    )


def _config(**overrides: Any) -> StrategyConfig:
    """构造最小策略配置，允许按关键字覆盖字段。"""
    base: dict[str, Any] = {
        "strategy_id": "t_fix",
        "display_name": "t_fix",
        "score": ScoreConfig(factors={"val": 1.0}),
    }
    base.update(overrides)
    return StrategyConfig(**base)


class TestConfigEnumsRejected:
    """配置枚举写错在解析期即失败，而不是静默退化为另一种行为。"""

    def test_sort_by_non_score_rejected_with_hint(self) -> None:
        """sort_by 只支持 score，报错信息给出替代方案。"""
        with pytest.raises(ValidationError, match="score.factors"):
            RankConfig(sort_by="momentum_rank")

    @pytest.mark.parametrize(
        "builder",
        [
            lambda: RankConfig(order="DESC"),
            lambda: FilterConfig(logic="and"),
            lambda: ScoreConfig(factors={"a": 1.0}, missing_factor_strategy="skip"),
            lambda: ScoreConfig(factors={"a": 1.0}, scoring_mode="Rank"),
            lambda: PortfolioConfig(method="equal"),
            lambda: FilterRule(factor="a", op="gt", value=1.0, missing_strategy="skip"),
        ],
    )
    def test_enum_typos_rejected(self, builder: Any) -> None:
        """历史上 order="DESC" 会按升序选出最差资产、logic="and" 会按 OR 过滤。"""
        with pytest.raises(ValidationError):
            builder()

    def test_unknown_regime_key_rejected(self) -> None:
        """regime_rules 的 key 只能是三种 regime。"""
        with pytest.raises(ValidationError):
            _config(regime_rules={"bull": RegimeRuleConfig()})


class TestStructuralValidationCoversRegimeBlocks:
    """regime 覆盖块与顶层共用同一套跨字段校验。"""

    def test_regime_filter_and_rank_errors_reported(self) -> None:
        """regime 覆盖里的 between 缺上下限、top_n/bottom_n 同设都应报错。"""
        config = _config(
            timing=TimingConfig(factors={"t": 1.0}),
            regime_rules={
                "defensive": RegimeRuleConfig(
                    filters=FilterConfig(rules=[FilterRule(factor="val", op="between", value=1.0)]),
                    rank=RankConfig(top_n=1, bottom_n=1),
                )
            },
        )
        errors, _warnings = StrategyConfigService._structural_validation(config)
        assert any(e.startswith("regime_rules.defensive.filters.rules[0]") for e in errors)
        assert any(e.startswith("regime_rules.defensive.rank") for e in errors)

    def test_regime_rules_without_timing_warns(self) -> None:
        """未配置择时时 regime 覆盖永远不生效，应给出提示。"""
        config = _config(regime_rules={"offensive": RegimeRuleConfig()})
        _errors, warnings = StrategyConfigService._structural_validation(config)
        assert any("regime_rules" in w and "timing" in w for w in warnings)

    def test_winner_take_all_capped_by_max_asset_weight_warns(self) -> None:
        """赢家通吃且单资产上限低于目标仓位时提示会被裁剪。"""
        config = _config(
            portfolio=PortfolioConfig(method="winner_take_all", default_exposure=0.8),
            risk=RiskConfig(max_asset_weight=0.3),
        )
        _errors, warnings = StrategyConfigService._structural_validation(config)
        assert any("winner_take_all" in w for w in warnings)


class TestZeroMissingIsWorst:
    """zero 缺失策略按最差处理。"""

    def test_absolute_zero_with_inverting_transform_scores_lowest(self) -> None:
        """缺失 + invert_percentile 不能拿到满分（历史缺陷：先置 0 再变换得 100）。"""
        config = _config(
            score=ScoreConfig(
                factors={"val": 1.0},
                transforms={"val": "invert_percentile"},
                missing_factor_strategy="zero",
            )
        )
        factors: dict[tuple[str, str], float | None] = {
            ("A", "val"): None,
            ("B", "val"): 90.0,
            ("C", "val"): 50.0,
            ("D", "val"): 70.0,
        }
        result = StrategyEngine().run(config, _context(factors), include_details=False)
        assert result.scores["A"] == 0.0
        assert min(result.scores, key=result.scores.__getitem__) == "A"


class TestEngineExclusion:
    """评分前的统一剔除（实时与回测共用）。"""

    @staticmethod
    def _factors() -> dict[tuple[str, str], float | None]:
        """A 缺 val，其余资产齐全；所有资产都有 mom。"""
        factors: dict[tuple[str, str], float | None] = {}
        for i, code in enumerate(CODES):
            factors[(code, "val")] = None if code == "A" else float(10 * (i + 1))
            factors[(code, "mom")] = float(i)
        return factors

    def test_exclude_strategy_removes_asset_before_scoring(self) -> None:
        """exclude 策略：缺失资产被剔除并记录原因，且不参与横截面标准化。"""
        config = _config(
            score=ScoreConfig(
                factors={"val": 1.0}, missing_factor_strategy="exclude", scoring_mode="zscore"
            )
        )
        result = StrategyEngine().run(config, _context(self._factors()), include_details=False)
        assert result.excluded == {"A": "MISSING_FACTOR"}
        assert "A" not in result.scores

    def test_ignore_strategy_keeps_asset(self) -> None:
        """ignore 策略：缺失交给评分器处理，不在评分前一刀切。"""
        config = _config(score=ScoreConfig(factors={"val": 1.0, "mom": 1.0}))
        result = StrategyEngine().run(config, _context(self._factors()), include_details=False)
        assert result.excluded == {}
        assert "A" in result.scores

    def test_filter_pass_and_or_logic_not_excluded(self) -> None:
        """过滤规则 missing_strategy=pass 或 OR 逻辑下，缺失不导致提前剔除。"""
        for filters in (
            FilterConfig(
                rules=[FilterRule(factor="val", op="gt", value=0.0, missing_strategy="pass")]
            ),
            FilterConfig(
                logic="OR",
                rules=[
                    FilterRule(factor="val", op="gt", value=0.0),
                    FilterRule(factor="mom", op="gte", value=0.0),
                ],
            ),
        ):
            config = _config(score=ScoreConfig(factors={"mom": 1.0}), filters=filters)
            result = StrategyEngine().run(config, _context(self._factors()), include_details=False)
            assert result.excluded == {}
            assert "A" in result.scores

    def test_only_effective_regime_factors_are_required(self) -> None:
        """其他 regime 专用的因子缺失不应剔除资产。"""
        config = _config(
            score=ScoreConfig(factors={"mom": 1.0}, missing_factor_strategy="exclude"),
            timing=TimingConfig(factors={"t": 1.0}),
            regime_rules={
                "defensive": RegimeRuleConfig(
                    score=ScoreConfig(factors={"val": 1.0}, missing_factor_strategy="exclude")
                )
            },
        )
        offensive = _context(self._factors(), market_factors={"t": 90.0})
        result = StrategyEngine().run(config, offensive, include_details=False)
        assert result.timing is not None and result.timing.regime == "offensive"
        assert result.excluded == {}

        defensive = _context(self._factors(), market_factors={"t": 10.0})
        result = StrategyEngine().run(config, defensive, include_details=False)
        assert result.excluded == {"A": "MISSING_FACTOR"}

    def test_market_shape_refs_are_exempt(self) -> None:
        """市场级引用没有逐资产取值，不作为剔除依据。"""
        config = _config(
            score=ScoreConfig(factors={"mom": 1.0}),
            filters=FilterConfig(rules=[FilterRule(factor="breadth", op="gt", value=0.0)]),
        )
        factors = self._factors()
        result = StrategyEngine().run(
            config, _context(factors, market_shape_refs={"breadth"}), include_details=False
        )
        assert result.excluded == {}

    def test_live_strategy_results_still_cover_full_universe(self) -> None:
        """被剔除资产仍在逐资产结果中落一行（实时 index_signal 不缺行）。"""
        config = _config(score=ScoreConfig(factors={"val": 1.0}, missing_factor_strategy="exclude"))
        result = StrategyEngine().run(config, _context(self._factors()), include_details=True)
        assert sorted(r.index_code for r in result.strategy_results) == CODES


class TestTimingTargetExposure:
    """择时目标仓位只由 regime 与组合级风控决定。"""

    def test_independent_of_selection(self) -> None:
        """过滤后无入选资产时 total_exposure 为 0，但择时目标仓位不受影响。"""
        config = _config(
            timing=TimingConfig(factors={"t": 1.0}),
            filters=FilterConfig(rules=[FilterRule(factor="val", op="gt", value=1e9)]),
            portfolio=PortfolioConfig(
                method="equal_weight",
                timing_exposure={"offensive": 0.9, "neutral": 0.5, "defensive": 0.2},
            ),
            risk=RiskConfig(max_asset_weight=0.3, max_portfolio_exposure=0.8),
        )
        factors = {(code, "val"): 1.0 for code in CODES}
        result = StrategyEngine().run(
            config, _context(factors, market_factors={"t": 90.0}), include_details=False
        )
        assert result.positions == {}
        assert result.total_exposure == 0.0
        # 0.9 被组合级上限 0.8 裁剪
        assert result.timing_target_exposure == 0.8
        assert result.max_asset_weight == 0.3

    def test_all_timing_factors_missing_marks_degraded(self) -> None:
        """择时因子全部缺失：回退 neutral 并标记降级，供服务层告警。"""
        config = _config(
            timing=TimingConfig(
                factors={"t": 1.0}, thresholds=TimingThresholds(offensive=65, defensive=35)
            )
        )
        factors = {(code, "val"): 1.0 for code in CODES}
        result = StrategyEngine().run(
            config, _context(factors, market_factors={"t": None}), include_details=False
        )
        assert result.timing is not None
        assert result.timing.regime == "neutral"
        assert result.timing.factors["degraded"] is True

    def test_effective_scoring_mode_follows_regime_override(self) -> None:
        """regime 覆盖评分模式时结果携带生效模式（回测信号等级据此判定）。"""
        config = _config(
            timing=TimingConfig(factors={"t": 1.0}),
            regime_rules={
                "offensive": RegimeRuleConfig(
                    score=ScoreConfig(factors={"val": 1.0}, scoring_mode="zscore")
                )
            },
        )
        factors = {(code, "val"): float(i) for i, code in enumerate(CODES)}
        result = StrategyEngine().run(
            config, _context(factors, market_factors={"t": 90.0}), include_details=False
        )
        assert result.effective_scoring_mode == "zscore"


class TestRankDeterminism:
    """排名的确定性与子排名口径。"""

    def test_ties_broken_by_index_code(self) -> None:
        """得分相同按代码升序决定先后，与 universe 输入顺序无关。"""
        config = _config(rank=RankConfig(top_n=2))
        factors = {(code, "val"): 50.0 for code in CODES}
        ctx = _context(factors)
        ctx.universe = list(reversed(ctx.universe))
        result = StrategyEngine().run(config, ctx, include_details=False)
        assert [r.index_code for r in result.rankings] == ["A", "B"]

    def test_valuation_sub_rank_uses_raw_ascending(self) -> None:
        """估值子排名按原值升序（数值越低越靠前），不再假设 100 − 值。"""
        config = _config(rank=RankConfig(valuation_factor="pe"))
        factors: dict[tuple[str, str], float | None] = {}
        for code, pe in zip(CODES, [30.0, 8.0, 15.0, 120.0]):
            factors[(code, "val")] = 50.0
            factors[(code, "pe")] = pe
        result = StrategyEngine().run(config, _context(factors), include_details=False)
        ranks = {r.index_code: r.valuation_rank for r in result.rankings}
        assert ranks == {"B": 1, "C": 2, "A": 3, "D": 4}

    def test_category_not_fabricated(self) -> None:
        """无分类数据时排名分类留空，不再伪造"宽基"。"""
        factors = {(code, "val"): 1.0 for code in CODES}
        result = StrategyEngine().run(_config(), _context(factors), include_details=False)
        assert all(r.category == "" for r in result.rankings)


class TestPlanMode:
    """星标摘要的双腿计划类型。"""

    @pytest.mark.parametrize(
        "selection,risk,expected",
        [
            (True, True, "full"),
            (True, False, "selection_only"),
            (False, True, "risk_only"),
            (False, False, "hold"),
        ],
    )
    def test_plan_mode_mapping(self, selection: bool, risk: bool, expected: str) -> None:
        """四象限与回测双腿合成口径一致。"""
        assert _plan_mode(selection, risk) == expected


class TestSnapshotNeverFallsBack:
    """快照损坏时回测必须失败，不得改用当前配置。"""

    def test_corrupt_snapshot_fails_backtest(self) -> None:
        """有快照但解析失败：标记失败且不读取当前配置。"""
        repo = mock.MagicMock()
        repo.find_by_id.return_value = mock.MagicMock(
            config_snapshot={"config_json": {"score": "not-a-dict"}}
        )
        repo.find_latest_daily_date.return_value = None
        svc = BacktestService(db=mock.MagicMock(), backtest_repo=repo)
        with mock.patch.object(StrategyConfigService, "get_parsed_config") as live_config:
            svc.run_backtest("bt-corrupt")
        live_config.assert_not_called()
        repo.mark_failed.assert_called_once()
        assert "快照解析失败" in repo.mark_failed.call_args[0][1]


class TestMissingFactorReporting:
    """缺失与失败信息的完整呈现。"""

    def test_insufficient_factors_include_timing_and_skip_display(self) -> None:
        """择时因子缺失需告警；仅展示用的排名子因子缺失不告警。"""
        builder = ContextBuilder(db=mock.MagicMock(), factor_provider=mock.MagicMock())
        ctx = _context(
            {("A", "val"): 1.0, ("A", "pe"): None},
            market_factors={"t": None},
        )
        ctx.universe = [{"index_code": "A", "name_cn": "A"}]
        ctx.display_only_refs = {"pe"}
        missing = builder.insufficient_factors(ctx)
        assert set(missing) == {"t"}

    def test_compute_failure_warnings(self) -> None:
        """计算失败的引用生成 COMPUTE_FAILED 警告（与数据不足区分）。"""
        warnings = collect_compute_failure_warnings({"b", "a"})
        assert [w.code for w in warnings] == ["COMPUTE_FAILED", "COMPUTE_FAILED"]
        assert "因子 a" in warnings[0].message


class TestReturnDeterminism:
    """组合收益与换手的浮点累加顺序与集合迭代顺序无关。"""

    def test_rebalance_return_and_turnover_iterate_sorted(self) -> None:
        """源码层面固定按代码排序遍历（集合顺序随进程哈希种子变化）。"""
        import inspect

        from quant_etf_api.domain.portfolio import returns, turnover

        assert "sorted(set(old_positions) | set(new_positions))" in inspect.getsource(
            returns.compute_rebalance_day_return
        )
        assert "sorted(" in inspect.getsource(turnover.compute_turnover)
