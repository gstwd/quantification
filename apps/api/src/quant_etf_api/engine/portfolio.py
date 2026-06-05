"""组合构建模块：根据排名和择时信号分配目标仓位权重。

支持 equal_weight（等权）和 score_weight（得分加权）两种方法。
"""

from __future__ import annotations

import logging
from typing import Protocol

from quant_etf_api.domain.strategies.models import AssetRanking, TimingSignal
from quant_etf_api.engine.config import PortfolioConfig

logger = logging.getLogger(__name__)

# 默认择时 regime 对应的总仓位
_DEFAULT_TIMING_EXPOSURE: dict[str, float] = {
    "offensive": 0.80,
    "neutral": 0.50,
    "defensive": 0.20,
}


class WeightAllocator(Protocol):
    """权重分配器协议。"""

    def allocate(
        self,
        config: PortfolioConfig,
        rankings: list[AssetRanking],
        timing: TimingSignal | None = None,
    ) -> dict[str, float]:
        """分配目标仓位权重。

        Args:
            config: 组合配置。
            rankings: 资产排名列表（已排序）。
            timing: 择时信号，None 时默认中性。

        Returns:
            key=etf_code, value=目标权重（0-1）。
        """
        ...


class EqualWeightAllocator:
    """等权分配器。"""

    def allocate(
        self,
        config: PortfolioConfig,
        rankings: list[AssetRanking],
        timing: TimingSignal | None = None,
    ) -> dict[str, float]:
        """等权分配。"""
        if not rankings:
            return {}

        total_exposure = self._get_total_exposure(config, timing)
        n = len(rankings)
        weight = total_exposure / n

        return {r.etf_code: round(weight, 4) for r in rankings}

    def _get_total_exposure(
        self, config: PortfolioConfig, timing: TimingSignal | None
    ) -> float:
        """根据择时信号确定总仓位上限。"""
        if timing and config.timing_exposure:
            return config.timing_exposure.get(timing.regime, 0.50)
        if timing:
            return _DEFAULT_TIMING_EXPOSURE.get(timing.regime, 0.50)
        return 0.50


class ScoreWeightAllocator:
    """得分加权分配器。"""

    def allocate(
        self,
        config: PortfolioConfig,
        rankings: list[AssetRanking],
        timing: TimingSignal | None = None,
    ) -> dict[str, float]:
        """按得分加权分配。"""
        if not rankings:
            return {}

        total_exposure = self._get_total_exposure(config, timing)

        # 过滤得分为 0 或负数的
        eligible = [r for r in rankings if r.score > 0]
        if not eligible:
            return {}

        total_score = sum(r.score for r in eligible)
        if total_score == 0:
            return {}

        positions: dict[str, float] = {}
        for r in eligible:
            weight = (r.score / total_score) * total_exposure
            positions[r.etf_code] = round(weight, 4)

        return positions

    def _get_total_exposure(
        self, config: PortfolioConfig, timing: TimingSignal | None
    ) -> float:
        """根据择时信号确定总仓位上限。"""
        if timing and config.timing_exposure:
            return config.timing_exposure.get(timing.regime, 0.50)
        if timing:
            return _DEFAULT_TIMING_EXPOSURE.get(timing.regime, 0.50)
        return 0.50


def build_allocator(method: str) -> WeightAllocator:
    """根据方法名构建权重分配器。

    Args:
        method: 分配方法，equal_weight / score_weight。

    Returns:
        权重分配器实例。

    Raises:
        ValueError: 未知的分配方法。
    """
    allocators: dict[str, WeightAllocator] = {
        "equal_weight": EqualWeightAllocator(),
        "score_weight": ScoreWeightAllocator(),
    }
    if method not in allocators:
        raise ValueError(f"未知的权重分配方法: {method}，可用: {list(allocators.keys())}")
    return allocators[method]
