"""组合构建模块：根据排名和择时信号分配目标仓位权重。

支持 equal_weight（等权）和 score_weight（得分加权）两种方法。
"""

from __future__ import annotations

import logging
from typing import Protocol

from quant_etf_api.domain.strategies.models import AssetRanking, TimingSignal
from quant_etf_api.engine.config import PortfolioConfig, RiskConfig

logger = logging.getLogger(__name__)

# 默认择时 regime 对应的总仓位
_DEFAULT_TIMING_EXPOSURE: dict[str, float] = {
    "offensive": 0.80,
    "neutral": 0.50,
    "defensive": 0.20,
}


def resolve_target_exposure(config: PortfolioConfig, timing: TimingSignal | None) -> float:
    """根据择时信号确定总仓位上限（各分配器共用的唯一实现）。

    Args:
        config: 组合配置。
        timing: 择时信号，None 表示未配置择时。

    Returns:
        目标总仓位（0-1）：配置了 timing_exposure 时按 regime 取值，否则使用默认
        regime 仓位表；未配置择时使用 default_exposure。
    """
    if timing and config.timing_exposure:
        return config.timing_exposure.get(timing.regime, config.default_exposure)
    if timing:
        return _DEFAULT_TIMING_EXPOSURE.get(timing.regime, config.default_exposure)
    return config.default_exposure


def cap_target_exposure(exposure: float, risk: RiskConfig | None) -> float:
    """按组合级风控上限裁剪目标总仓位。

    只施加组合级约束（总仓位上限与最低现金比例）；单资产上限依赖具体成分，
    由调用方在持仓层面裁剪。

    Args:
        exposure: 择时决定的目标总仓位。
        risk: 风控配置，None 表示未配置。

    Returns:
        裁剪后的目标总仓位（4 位小数）。
    """
    if risk is None:
        return round(exposure, 4)
    return round(
        min(exposure, risk.max_portfolio_exposure, 1.0 - risk.min_cash_ratio),
        4,
    )


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
            key=index_code, value=目标权重（0-1）。
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

        total_exposure = resolve_target_exposure(config, timing)
        n = len(rankings)
        weight = total_exposure / n

        return {r.index_code: round(weight, 4) for r in rankings}


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

        total_exposure = resolve_target_exposure(config, timing)

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
            positions[r.index_code] = round(weight, 4)

        return positions


class WinnerTakeAllAllocator:
    """赢家通吃分配器。

    将全部仓位分配给排名第一的资产，适用于二八轮动等集中持仓策略。
    """

    def allocate(
        self,
        config: PortfolioConfig,
        rankings: list[AssetRanking],
        timing: TimingSignal | None = None,
    ) -> dict[str, float]:
        """赢家通吃分配。

        Args:
            config: 组合配置。
            rankings: 资产排名列表（已排序）。
            timing: 择时信号。

        Returns:
            仅包含排名第一资产的仓位字典。
        """
        if not rankings:
            return {}

        total_exposure = resolve_target_exposure(config, timing)
        winner = rankings[0]

        return {winner.index_code: round(total_exposure, 4)}


def build_allocator(method: str) -> WeightAllocator:
    """根据方法名构建权重分配器。

    Args:
        method: 分配方法，equal_weight / score_weight / winner_take_all。

    Returns:
        权重分配器实例。

    Raises:
        ValueError: 未知的分配方法。
    """
    allocators: dict[str, WeightAllocator] = {
        "equal_weight": EqualWeightAllocator(),
        "score_weight": ScoreWeightAllocator(),
        "winner_take_all": WinnerTakeAllAllocator(),
    }
    if method not in allocators:
        raise ValueError(f"未知的权重分配方法: {method}，可用: {list(allocators.keys())}")
    return allocators[method]
