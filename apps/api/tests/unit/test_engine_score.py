"""评分模块单元测试。

覆盖 DefaultScoreCalculator 的核心逻辑：
- 因子加权计算
- 变换函数
- 缺失因子策略
- 择时评分
"""

from __future__ import annotations

from datetime import date


from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import ScoreConfig, TimingConfig, TimingThresholds
from quant_etf_api.engine.score import (
    DefaultScoreCalculator,
    _clamp_0_100,
    _invert_percentile,
    _momentum_score,
    _trend_score,
    _volume_score,
)


def _make_context(
    asset_factors: dict[tuple[str, str], float | None] | None = None,
    market_factors: dict[str, float | None] | None = None,
    universe: list[dict] | None = None,
) -> EngineContext:
    """构建测试用上下文。"""
    return EngineContext(
        trade_date=date(2025, 1, 15),
        universe=universe or [
            {"etf_code": "510300", "name_cn": "沪深300ETF", "category": "broad_index"},
            {"etf_code": "510500", "name_cn": "中证500ETF", "category": "broad_index"},
        ],
        asset_factors=asset_factors or {},
        market_factors=market_factors or {},
    )


class TestTransformFunctions:
    """变换函数测试。"""

    def test_invert_percentile(self) -> None:
        """百分位反转：100 - value。"""
        assert _invert_percentile(30.0) == 70.0
        assert _invert_percentile(80.0) == 20.0
        assert _invert_percentile(50.0) == 50.0

    def test_momentum_score(self) -> None:
        """收益率映射为动量得分。"""
        assert _momentum_score(20.0) == 95.0
        assert _momentum_score(12.0) == 85.0
        assert _momentum_score(7.0) == 70.0
        assert _momentum_score(3.0) == 60.0
        assert _momentum_score(1.0) == 50.0
        assert _momentum_score(-1.0) == 40.0
        assert _momentum_score(-3.0) == 30.0
        assert _momentum_score(-7.0) == 20.0
        assert _momentum_score(-15.0) == 10.0

    def test_volume_score(self) -> None:
        """量比映射为量能得分。"""
        assert _volume_score(0.2) == 10.0
        assert _volume_score(0.4) == 20.0
        assert _volume_score(0.6) == 35.0
        assert _volume_score(0.9) == 50.0
        assert _volume_score(1.1) == 70.0
        assert _volume_score(1.4) == 80.0
        assert _volume_score(1.8) == 85.0
        assert _volume_score(2.5) == 70.0
        assert _volume_score(5.0) == 50.0

    def test_trend_score(self) -> None:
        """MA偏离度映射为趋势得分。"""
        assert _trend_score(-15.0) == 0.0
        assert _trend_score(15.0) == 100.0
        assert _trend_score(0.0) == 50.0
        assert _trend_score(5.0) == 75.0
        assert _trend_score(-5.0) == 25.0

    def test_clamp_0_100(self) -> None:
        """通用裁剪。"""
        assert _clamp_0_100(150.0) == 100.0
        assert _clamp_0_100(-50.0) == 0.0
        assert _clamp_0_100(50.0) == 50.0


class TestDefaultScoreCalculator:
    """默认评分计算器测试。"""

    def test_basic_scoring(self) -> None:
        """基础评分：两个因子加权。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"momentum": 0.6, "valuation": 0.4},
            transforms={},
        )
        context = _make_context(
            asset_factors={
                ("510300", "momentum"): 80.0,
                ("510300", "valuation"): 60.0,
                ("510500", "momentum"): 50.0,
                ("510500", "valuation"): 70.0,
            },
        )
        scores = calc.calculate(config, context)

        # 510300: (80*0.6 + 60*0.4) / (0.6+0.4) = 72.0
        assert scores["510300"] == 72.0
        # 510500: (50*0.6 + 70*0.4) / (0.6+0.4) = 58.0
        assert scores["510500"] == 58.0

    def test_with_transforms(self) -> None:
        """使用变换函数。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"return_20d": 0.6, "pe_percentile": 0.4},
            transforms={
                "return_20d": "momentum_score",
                "pe_percentile": "invert_percentile",
            },
        )
        context = _make_context(
            asset_factors={
                ("510300", "return_20d"): 12.0,  # momentum_score -> 85
                ("510300", "pe_percentile"): 30.0,  # invert_percentile -> 70
            },
        )
        scores = calc.calculate(config, context)

        # 510300: (85*0.6 + 70*0.4) / 1.0 = 79.0
        assert scores["510300"] == 79.0

    def test_missing_factor_ignore(self) -> None:
        """缺失因子策略：ignore（忽略并重新归一化）。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            missing_factor_strategy="ignore",
        )
        context = _make_context(
            asset_factors={
                ("510300", "a"): 80.0,
                ("510300", "b"): None,  # 缺失
            },
        )
        scores = calc.calculate(config, context)

        # 忽略 b，仅用 a: 80.0
        assert scores["510300"] == 80.0

    def test_missing_factor_zero(self) -> None:
        """缺失因子策略：zero（按 0 处理）。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            missing_factor_strategy="zero",
        )
        context = _make_context(
            asset_factors={
                ("510300", "a"): 80.0,
                ("510300", "b"): None,  # 缺失，按 0 处理
            },
        )
        scores = calc.calculate(config, context)

        # (80*0.6 + 0*0.4) / 1.0 = 48.0
        assert scores["510300"] == 48.0

    def test_missing_factor_exclude(self) -> None:
        """缺失因子策略：exclude（排除该资产）。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            missing_factor_strategy="exclude",
        )
        context = _make_context(
            asset_factors={
                ("510300", "a"): 80.0,
                ("510300", "b"): None,  # 缺失，排除
                ("510500", "a"): 70.0,
                ("510500", "b"): 60.0,
            },
        )
        scores = calc.calculate(config, context)

        # 510300 被排除
        assert "510300" not in scores
        assert "510500" in scores

    def test_negative_weights(self) -> None:
        """支持负权重。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"a": 0.6, "b": -0.4},
        )
        context = _make_context(
            asset_factors={
                ("510300", "a"): 80.0,
                ("510300", "b"): 30.0,  # 负权重，低值更好
            },
        )
        scores = calc.calculate(config, context)

        # (80*0.6 + 30*(-0.4)) / (0.6+0.4) = (48-12)/1 = 36.0
        assert scores["510300"] == 36.0

    def test_score_clamped_0_100(self) -> None:
        """得分限制在 0-100 范围。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(factors={"a": 1.0})
        context = _make_context(
            asset_factors={
                ("510300", "a"): 150.0,  # 超出范围
                ("510500", "a"): -50.0,  # 低于范围
            },
        )
        scores = calc.calculate(config, context)

        assert scores["510300"] == 100.0
        assert scores["510500"] == 0.0


class TestTimingCalculation:
    """择时评分测试。"""

    def test_offensive_regime(self) -> None:
        """高综合得分 → 进攻 regime。"""
        calc = DefaultScoreCalculator()
        config = TimingConfig(
            factors={"pe_percentile": 0.5, "pb_percentile": 0.5},
            transforms={
                "pe_percentile": "invert_percentile",
                "pb_percentile": "invert_percentile",
            },
            thresholds=TimingThresholds(offensive=65, defensive=35),
        )
        context = _make_context(
            market_factors={"pe_percentile": 20.0, "pb_percentile": 25.0},
        )

        score, regime, details = calc.calculate_timing(config, context)

        # invert: 80 + 75 = 155, avg = 77.5
        assert score >= 65
        assert regime == "offensive"

    def test_defensive_regime(self) -> None:
        """低综合得分 → 防守 regime。"""
        calc = DefaultScoreCalculator()
        config = TimingConfig(
            factors={"pe_percentile": 0.5, "pb_percentile": 0.5},
            transforms={
                "pe_percentile": "invert_percentile",
                "pb_percentile": "invert_percentile",
            },
            thresholds=TimingThresholds(offensive=65, defensive=35),
        )
        context = _make_context(
            market_factors={"pe_percentile": 85.0, "pb_percentile": 90.0},
        )

        score, regime, details = calc.calculate_timing(config, context)

        # invert: 15 + 10 = 25, avg = 12.5
        assert score <= 35
        assert regime == "defensive"

    def test_neutral_regime(self) -> None:
        """中等综合得分 → 观望 regime。"""
        calc = DefaultScoreCalculator()
        config = TimingConfig(
            factors={"pe_percentile": 1.0},
            transforms={"pe_percentile": "invert_percentile"},
            thresholds=TimingThresholds(offensive=65, defensive=35),
        )
        context = _make_context(
            market_factors={"pe_percentile": 50.0},
        )

        score, regime, details = calc.calculate_timing(config, context)

        # invert: 50
        assert regime == "neutral"

    def test_missing_factor_returns_neutral(self) -> None:
        """所有因子缺失 → 返回中性。"""
        calc = DefaultScoreCalculator()
        config = TimingConfig(
            factors={"pe_percentile": 1.0},
            thresholds=TimingThresholds(offensive=65, defensive=35),
        )
        context = _make_context(market_factors={})

        score, regime, details = calc.calculate_timing(config, context)

        assert score == 0.0
        assert regime == "neutral"
        assert "reason" in details
