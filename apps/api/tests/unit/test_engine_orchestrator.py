"""策略引擎编排器单元测试。

覆盖 StrategyEngine 的核心逻辑：
- 完整管线执行
- 信号模式 vs 配置模式
- 择时集成
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
    TimingConfig,
    TimingThresholds,
)
from quant_etf_api.engine.orchestrator import StrategyEngine


def _make_context(
    asset_factors: dict[tuple[str, str], float | None] | None = None,
    market_factors: dict[str, float | None] | None = None,
    asset_metadata: dict[str, dict] | None = None,
) -> EngineContext:
    """构建测试用上下文。"""
    return EngineContext(
        trade_date=date(2025, 1, 15),
        universe=[
            {"etf_code": "510300", "name_cn": "沪深300ETF", "category": "broad_index"},
            {"etf_code": "510500", "name_cn": "中证500ETF", "category": "broad_index"},
            {"etf_code": "159915", "name_cn": "创业板ETF", "category": "broad_index"},
        ],
        asset_factors=asset_factors or {},
        market_factors=market_factors or {},
        asset_metadata=asset_metadata or {},
    )


class TestStrategyEngine:
    """策略引擎测试。"""

    def test_signal_mode(self) -> None:
        """信号模式：无 portfolio 配置，只输出得分和排名。"""
        engine = StrategyEngine()
        config = StrategyConfig(
            strategy_id="test_signal",
            display_name="测试信号策略",
            score=ScoreConfig(
                factors={"momentum": 0.6, "valuation": 0.4},
            ),
            rank=RankConfig(sort_by="score", order="desc", top_n=3),
        )
        context = _make_context(
            asset_factors={
                ("510300", "momentum"): 80.0,
                ("510300", "valuation"): 60.0,
                ("510500", "momentum"): 70.0,
                ("510500", "valuation"): 50.0,
                ("159915", "momentum"): 60.0,
                ("159915", "valuation"): 40.0,
            },
        )

        result = engine.run(config, context)

        assert result.strategy_id == "test_signal"
        assert result.timing is None
        assert len(result.scores) == 3
        assert len(result.rankings) == 3
        assert result.positions == {}  # 信号模式无仓位
        assert result.total_exposure == 0.0
        assert result.cash_ratio == 1.0

    def test_allocation_mode(self) -> None:
        """配置模式：有 portfolio 配置，输出仓位。"""
        engine = StrategyEngine()
        config = StrategyConfig(
            strategy_id="test_alloc",
            display_name="测试配置策略",
            score=ScoreConfig(
                factors={"momentum": 0.6, "valuation": 0.4},
            ),
            rank=RankConfig(sort_by="score", order="desc", top_n=3),
            portfolio=PortfolioConfig(
                method="equal_weight",
                timing_exposure={"offensive": 0.80, "neutral": 0.50, "defensive": 0.20},
            ),
        )
        context = _make_context(
            asset_factors={
                ("510300", "momentum"): 80.0,
                ("510300", "valuation"): 60.0,
                ("510500", "momentum"): 70.0,
                ("510500", "valuation"): 50.0,
                ("159915", "momentum"): 60.0,
                ("159915", "valuation"): 40.0,
            },
        )

        result = engine.run(config, context)

        assert result.strategy_id == "test_alloc"
        assert len(result.positions) > 0
        assert result.total_exposure > 0
        assert result.cash_ratio < 1.0

    def test_with_timing(self) -> None:
        """带择时的完整管线。"""
        engine = StrategyEngine()
        config = StrategyConfig(
            strategy_id="test_timing",
            display_name="测试择时策略",
            timing=TimingConfig(
                factors={"pe_percentile": 0.5, "pb_percentile": 0.5},
                transforms={
                    "pe_percentile": "invert_percentile",
                    "pb_percentile": "invert_percentile",
                },
                thresholds=TimingThresholds(offensive=65, defensive=35),
            ),
            score=ScoreConfig(
                factors={"momentum": 1.0},
            ),
            rank=RankConfig(sort_by="score", order="desc"),
            portfolio=PortfolioConfig(method="equal_weight"),
        )
        context = _make_context(
            asset_factors={
                ("510300", "momentum"): 80.0,
                ("510500", "momentum"): 70.0,
                ("159915", "momentum"): 60.0,
            },
            market_factors={"pe_percentile": 20.0, "pb_percentile": 25.0},
        )

        result = engine.run(config, context)

        assert result.timing is not None
        assert result.timing.regime == "offensive"  # 低估值 → 进攻
        assert result.timing.confidence > 0

    def test_with_risk(self) -> None:
        """带风控的管线。"""
        engine = StrategyEngine()
        config = StrategyConfig(
            strategy_id="test_risk",
            display_name="测试风控策略",
            score=ScoreConfig(factors={"momentum": 1.0}),
            rank=RankConfig(sort_by="score", order="desc"),
            portfolio=PortfolioConfig(method="equal_weight"),
            risk=RiskConfig(max_asset_weight=0.30),
        )
        context = _make_context(
            asset_factors={
                ("510300", "momentum"): 80.0,
                ("510500", "momentum"): 70.0,
                ("159915", "momentum"): 60.0,
            },
        )

        result = engine.run(config, context)

        # 单资产仓位不超过 30%
        for weight in result.positions.values():
            assert weight <= 0.30

    def test_strategy_results_compatibility(self) -> None:
        """兼容旧接口的 StrategyResult 列表。"""
        engine = StrategyEngine()
        config = StrategyConfig(
            strategy_id="test_compat",
            display_name="测试兼容策略",
            score=ScoreConfig(factors={"momentum": 1.0}),
            rank=RankConfig(sort_by="score", order="desc"),
        )
        context = _make_context(
            asset_factors={
                ("510300", "momentum"): 80.0,
                ("510500", "momentum"): 70.0,
                ("159915", "momentum"): 60.0,
            },
        )

        result = engine.run(config, context)

        assert len(result.strategy_results) == 3
        for r in result.strategy_results:
            assert r.strategy_id == "test_compat"
            assert r.signal_level in ("HIGH", "MID", "LOW")
            assert 0 <= r.signal_score <= 100
