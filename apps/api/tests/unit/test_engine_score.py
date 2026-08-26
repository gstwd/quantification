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
    CrossSectionScorer,
    DefaultScoreCalculator,
)
from quant_etf_api.engine.transforms import (
    clamp_0_100,
    invert_percentile,
    momentum_score,
    trend_score,
    volume_score,
)


def _make_context(
    asset_factors: dict[tuple[str, str], float | None] | None = None,
    market_factors: dict[str, float | None] | None = None,
    universe: list[dict] | None = None,
) -> EngineContext:
    """构建测试用上下文。"""
    return EngineContext(
        trade_date=date(2025, 1, 15),
        universe=universe
        or [
            {"index_code": "000300", "name_cn": "沪深300", "category": "broad_index"},
            {"index_code": "000905", "name_cn": "中证500", "category": "broad_index"},
        ],
        asset_factors=asset_factors or {},
        market_factors=market_factors or {},
    )


class TestTransformFunctions:
    """变换函数测试。"""

    def test_invert_percentile(self) -> None:
        """百分位反转：100 - value。"""
        assert invert_percentile(30.0) == 70.0
        assert invert_percentile(80.0) == 20.0
        assert invert_percentile(50.0) == 50.0

    def test_momentum_score(self) -> None:
        """收益率映射为动量得分。"""
        assert momentum_score(20.0) == 95.0
        assert momentum_score(12.0) == 85.0
        assert momentum_score(7.0) == 70.0
        assert momentum_score(3.0) == 60.0
        assert momentum_score(1.0) == 50.0
        assert momentum_score(-1.0) == 40.0
        assert momentum_score(-3.0) == 30.0
        assert momentum_score(-7.0) == 20.0
        assert momentum_score(-15.0) == 10.0

    def test_volume_score(self) -> None:
        """量比映射为量能得分。"""
        assert volume_score(0.2) == 10.0
        assert volume_score(0.4) == 20.0
        assert volume_score(0.6) == 35.0
        assert volume_score(0.9) == 50.0
        assert volume_score(1.1) == 70.0
        assert volume_score(1.4) == 80.0
        assert volume_score(1.8) == 85.0
        assert volume_score(2.5) == 70.0
        assert volume_score(5.0) == 50.0

    def test_trend_score(self) -> None:
        """MA偏离度映射为趋势得分。"""
        assert trend_score(-15.0) == 0.0
        assert trend_score(15.0) == 100.0
        assert trend_score(0.0) == 50.0
        assert trend_score(5.0) == 75.0
        assert trend_score(-5.0) == 25.0

    def test_clamp_0_100(self) -> None:
        """通用裁剪。"""
        assert clamp_0_100(150.0) == 100.0
        assert clamp_0_100(-50.0) == 0.0
        assert clamp_0_100(50.0) == 50.0


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
                ("000300", "momentum"): 80.0,
                ("000300", "valuation"): 60.0,
                ("000905", "momentum"): 50.0,
                ("000905", "valuation"): 70.0,
            },
        )
        scores = calc.calculate(config, context)

        # 000300: (80*0.6 + 60*0.4) / (0.6+0.4) = 72.0
        assert scores["000300"] == 72.0
        # 000905: (50*0.6 + 70*0.4) / (0.6+0.4) = 58.0
        assert scores["000905"] == 58.0

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
                ("000300", "return_20d"): 12.0,  # momentum_score -> 85
                ("000300", "pe_percentile"): 30.0,  # invert_percentile -> 70
            },
        )
        scores = calc.calculate(config, context)

        # 000300: (85*0.6 + 70*0.4) / 1.0 = 79.0
        assert scores["000300"] == 79.0

    def test_missing_factor_ignore(self) -> None:
        """缺失因子策略：ignore（忽略并重新归一化）。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            missing_factor_strategy="ignore",
        )
        context = _make_context(
            asset_factors={
                ("000300", "a"): 80.0,
                ("000300", "b"): None,  # 缺失
            },
        )
        scores = calc.calculate(config, context)

        # 忽略 b，仅用 a: 80.0
        assert scores["000300"] == 80.0

    def test_missing_factor_zero(self) -> None:
        """缺失因子策略：zero（按 0 处理）。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            missing_factor_strategy="zero",
        )
        context = _make_context(
            asset_factors={
                ("000300", "a"): 80.0,
                ("000300", "b"): None,  # 缺失，按 0 处理
            },
        )
        scores = calc.calculate(config, context)

        # (80*0.6 + 0*0.4) / 1.0 = 48.0
        assert scores["000300"] == 48.0

    def test_missing_factor_exclude(self) -> None:
        """缺失因子策略：exclude（排除该资产）。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            missing_factor_strategy="exclude",
        )
        context = _make_context(
            asset_factors={
                ("000300", "a"): 80.0,
                ("000300", "b"): None,  # 缺失，排除
                ("000905", "a"): 70.0,
                ("000905", "b"): 60.0,
            },
        )
        scores = calc.calculate(config, context)

        # 000300 被排除
        assert "000300" not in scores
        assert "000905" in scores

    def test_negative_weights(self) -> None:
        """支持负权重。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(
            factors={"a": 0.6, "b": -0.4},
        )
        context = _make_context(
            asset_factors={
                ("000300", "a"): 80.0,
                ("000300", "b"): 30.0,  # 负权重，低值更好
            },
        )
        scores = calc.calculate(config, context)

        # (80*0.6 + 30*(-0.4)) / (0.6+0.4) = (48-12)/1 = 36.0
        assert scores["000300"] == 36.0

    def test_score_clamped_0_100(self) -> None:
        """得分限制在 0-100 范围。"""
        calc = DefaultScoreCalculator()
        config = ScoreConfig(factors={"a": 1.0})
        context = _make_context(
            asset_factors={
                ("000300", "a"): 150.0,  # 超出范围
                ("000905", "a"): -50.0,  # 低于范围
            },
        )
        scores = calc.calculate(config, context)

        assert scores["000300"] == 100.0
        assert scores["000905"] == 0.0


class TestCrossSectionScorer:
    """横截面评分器测试（B7：全缺失资产排除语义与 absolute 一致）。"""

    def test_rank_excludes_all_missing_asset(self) -> None:
        """rank 模式：全部因子缺失的资产不进入得分池。"""
        calc = CrossSectionScorer()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            scoring_mode="rank",
        )
        context = _make_context(
            asset_factors={
                ("000300", "a"): 80.0,
                ("000300", "b"): 60.0,
                ("000905", "a"): None,  # 全部缺失
                ("000905", "b"): None,
            },
        )

        scores = calc.calculate(config, context)

        assert "000905" not in scores
        assert "000300" in scores

    def test_zscore_excludes_all_missing_asset(self) -> None:
        """zscore 模式：全部因子缺失的资产不进入得分池。"""
        calc = CrossSectionScorer()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            scoring_mode="zscore",
        )
        context = _make_context(
            asset_factors={
                ("000300", "a"): 80.0,
                ("000300", "b"): 60.0,
                ("000905", "a"): None,  # 全部缺失
                ("000905", "b"): None,
            },
        )

        scores = calc.calculate(config, context)

        assert "000905" not in scores
        assert "000300" in scores

    def test_zero_strategy_keeps_all_missing_asset(self) -> None:
        """zero 策略：全部因子缺失仍按 0 参与评分，排除语义不生效。"""
        calc = CrossSectionScorer()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            scoring_mode="rank",
            missing_factor_strategy="zero",
        )
        context = _make_context(
            asset_factors={
                ("000300", "a"): 80.0,
                ("000300", "b"): 60.0,
                ("000905", "a"): None,  # 全部缺失，按 0 处理
                ("000905", "b"): None,
            },
        )

        scores = calc.calculate(config, context)

        assert "000905" in scores
        # 未排除：正常参与排名，因 raw=0 得到最低排名分
        assert scores["000905"] < scores["000300"]

    def test_ignore_partial_missing_keeps_asset(self) -> None:
        """ignore 策略：部分因子缺失时资产仍参与评分。"""
        calc = CrossSectionScorer()
        config = ScoreConfig(
            factors={"a": 0.6, "b": 0.4},
            scoring_mode="rank",
        )
        context = _make_context(
            asset_factors={
                ("000300", "a"): 80.0,
                ("000300", "b"): None,  # 部分缺失
                ("000905", "a"): 50.0,
                ("000905", "b"): 60.0,
            },
        )

        scores = calc.calculate(config, context)

        assert "000300" in scores
        assert "000905" in scores

    def test_debug_marks_all_missing_excluded(self) -> None:
        """调试信息：全缺失资产标记 excluded 并给出原因。"""
        calc = CrossSectionScorer()
        config = ScoreConfig(
            factors={"a": 1.0},
            scoring_mode="rank",
        )
        context = _make_context(
            asset_factors={
                ("000300", "a"): 80.0,
                ("000905", "a"): None,  # 全部缺失
            },
        )

        debug: list = []
        calc.calculate(config, context, debug=debug)

        missing = next(d for d in debug if d.index_code == "000905")
        assert missing.excluded is True
        assert missing.exclude_reason == "因子数据全部缺失"
        assert missing.raw_score is None


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
