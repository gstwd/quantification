"""组合构建模块单元测试。

覆盖 EqualWeightAllocator 和 ScoreWeightAllocator 的核心逻辑：
- 等权分配
- 得分加权分配
- 择时仓位控制
"""

from __future__ import annotations

import pytest

from quant_etf_api.domain.strategies.models import AssetRanking, TimingSignal
from quant_etf_api.engine.config import PortfolioConfig
from quant_etf_api.engine.portfolio import (
    EqualWeightAllocator,
    ScoreWeightAllocator,
    build_allocator,
)


def _make_rankings(codes: list[str], scores: list[float]) -> list[AssetRanking]:
    """构建测试用排名列表。"""
    return [
        AssetRanking(etf_code=c, name_cn=c, category="宽基", score=s)
        for c, s in zip(codes, scores)
    ]


class TestEqualWeightAllocator:
    """等权分配器测试。"""

    def test_basic_equal_weight(self) -> None:
        """基础等权分配。"""
        allocator = EqualWeightAllocator()
        config = PortfolioConfig(method="equal_weight")
        rankings = _make_rankings(["A", "B", "C"], [80.0, 70.0, 60.0])
        timing = TimingSignal(regime="neutral", confidence=50, label="观望")

        positions = allocator.allocate(config, rankings, timing)

        # 中性 50%，三只等权：50% / 3 ≈ 16.67%
        assert len(positions) == 3
        assert abs(positions["A"] - positions["B"]) < 0.01
        assert abs(positions["B"] - positions["C"]) < 0.01
        total = sum(positions.values())
        assert abs(total - 0.50) < 0.01

    def test_offensive_high_exposure(self) -> None:
        """进攻信号 → 高仓位。"""
        allocator = EqualWeightAllocator()
        config = PortfolioConfig(method="equal_weight")
        rankings = _make_rankings(["A", "B"], [80.0, 70.0])
        timing = TimingSignal(regime="offensive", confidence=80, label="进攻")

        positions = allocator.allocate(config, rankings, timing)

        total = sum(positions.values())
        assert abs(total - 0.80) < 0.01

    def test_defensive_low_exposure(self) -> None:
        """防守信号 → 低仓位。"""
        allocator = EqualWeightAllocator()
        config = PortfolioConfig(method="equal_weight")
        rankings = _make_rankings(["A", "B"], [80.0, 70.0])
        timing = TimingSignal(regime="defensive", confidence=80, label="防守")

        positions = allocator.allocate(config, rankings, timing)

        total = sum(positions.values())
        assert abs(total - 0.20) < 0.01

    def test_custom_timing_exposure(self) -> None:
        """自定义择时仓位。"""
        allocator = EqualWeightAllocator()
        config = PortfolioConfig(
            method="equal_weight",
            timing_exposure={"offensive": 0.90, "neutral": 0.60, "defensive": 0.10},
        )
        rankings = _make_rankings(["A", "B"], [80.0, 70.0])
        timing = TimingSignal(regime="offensive", confidence=80, label="进攻")

        positions = allocator.allocate(config, rankings, timing)

        total = sum(positions.values())
        assert abs(total - 0.90) < 0.01

    def test_empty_rankings(self) -> None:
        """空排名返回空仓位。"""
        allocator = EqualWeightAllocator()
        config = PortfolioConfig(method="equal_weight")

        positions = allocator.allocate(config, [])

        assert positions == {}


class TestScoreWeightAllocator:
    """得分加权分配器测试。"""

    def test_basic_score_weight(self) -> None:
        """基础得分加权分配。"""
        allocator = ScoreWeightAllocator()
        config = PortfolioConfig(method="score_weight")
        rankings = _make_rankings(["A", "B"], [80.0, 40.0])
        timing = TimingSignal(regime="neutral", confidence=50, label="观望")

        positions = allocator.allocate(config, rankings, timing)

        # 中性 50%，按得分加权：A=80/(80+40)*50%=33.33%, B=40/(80+40)*50%=16.67%
        assert abs(positions["A"] - 0.3333) < 0.01
        assert abs(positions["B"] - 0.1667) < 0.01

    def test_zero_scores_excluded(self) -> None:
        """得分为 0 的资产被排除。"""
        allocator = ScoreWeightAllocator()
        config = PortfolioConfig(method="score_weight")
        rankings = _make_rankings(["A", "B", "C"], [80.0, 0.0, 60.0])
        timing = TimingSignal(regime="neutral", confidence=50, label="观望")

        positions = allocator.allocate(config, rankings, timing)

        assert "B" not in positions
        assert "A" in positions
        assert "C" in positions

    def test_empty_rankings(self) -> None:
        """空排名返回空仓位。"""
        allocator = ScoreWeightAllocator()
        config = PortfolioConfig(method="score_weight")

        positions = allocator.allocate(config, [])

        assert positions == {}


class TestBuildAllocator:
    """工厂函数测试。"""

    def test_build_equal_weight(self) -> None:
        """构建等权分配器。"""
        allocator = build_allocator("equal_weight")
        assert isinstance(allocator, EqualWeightAllocator)

    def test_build_score_weight(self) -> None:
        """构建得分加权分配器。"""
        allocator = build_allocator("score_weight")
        assert isinstance(allocator, ScoreWeightAllocator)

    def test_build_unknown_raises(self) -> None:
        """未知方法抛出异常。"""
        with pytest.raises(ValueError, match="未知的权重分配方法"):
            build_allocator("unknown_method")
