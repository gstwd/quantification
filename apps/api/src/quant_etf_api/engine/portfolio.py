"""组合构建模块：根据排名和择时信号分配目标仓位权重。

支持 equal_weight（等权）、score_weight（得分加权）、winner_take_all（赢家通吃）、
inverse_vol（波动率倒数加权）与 risk_parity（等风险贡献）。
"""

from __future__ import annotations

import logging
from typing import Protocol

from quant_etf_api.domain.portfolio.risk_model import covariance, risk_parity_weights
from quant_etf_api.domain.strategies.models import AssetRanking, TimingSignal
from quant_etf_api.engine.base import EngineContext
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
        目标总仓位（0-1）：择时命中多档仓位（``timing.tiers``）时取档位仓位；
        否则配置了 timing_exposure 时按 regime 取值，再否则使用默认 regime 仓位表；
        命中条件仓位上限（``timing.exposure_caps``）时再取二者较小值；
        未配置择时使用 default_exposure。
    """
    if timing is None:
        return config.default_exposure
    if timing.exposure is not None:
        # 档位仓位优先于按 regime 查表（含 regime_rules 覆盖的 timing_exposure）
        exposure = timing.exposure
    elif config.timing_exposure:
        exposure = config.timing_exposure.get(timing.regime, config.default_exposure)
    else:
        exposure = _DEFAULT_TIMING_EXPOSURE.get(timing.regime, config.default_exposure)
    if timing.exposure_cap is not None:
        # 条件仓位上限（如估值过高封顶），在组合级风控之前施加
        exposure = min(exposure, timing.exposure_cap)
    return exposure


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
        context: EngineContext | None = None,
    ) -> dict[str, float]:
        """分配目标仓位权重。

        Args:
            config: 组合配置。
            rankings: 资产排名列表（已排序）。
            timing: 择时信号，None 时默认中性。
            context: 引擎上下文（inverse_vol 读波动率因子、risk_parity 读收益面板）；
                其余分配器忽略。

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
        context: EngineContext | None = None,
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
        context: EngineContext | None = None,
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
        context: EngineContext | None = None,
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


def _scale_raw_weights(raw: dict[str, float], total_exposure: float) -> dict[str, float]:
    """把非负原始权重归一化后缩放到目标总仓位（4 位小数）。

    Args:
        raw: 资产 → 原始权重（如波动率倒数）。
        total_exposure: 目标总仓位。

    Returns:
        目标权重；原始权重之和非正时为空。
    """
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {code: round(weight / total * total_exposure, 4) for code, weight in raw.items()}


class InverseVolAllocator:
    """波动率倒数加权：权重 ∝ 1 / vol_factor。

    波动率因子缺失的资产已由引擎在评分前剔除（``decision_required_refs``）；
    非正值视为无效，不参与分配。
    """

    def allocate(
        self,
        config: PortfolioConfig,
        rankings: list[AssetRanking],
        timing: TimingSignal | None = None,
        context: EngineContext | None = None,
    ) -> dict[str, float]:
        """按波动率倒数分配。"""
        if not rankings or context is None or not config.vol_factor:
            return {}
        raw: dict[str, float] = {}
        for ranking in rankings:
            vol = context.asset_factors.get((ranking.index_code, config.vol_factor))
            if vol is not None and vol > 0:
                raw[ranking.index_code] = 1.0 / vol
        return _scale_raw_weights(raw, resolve_target_exposure(config, timing))


class RiskParityAllocator:
    """等风险贡献：按近 ``cov_window`` 个交易日的日收益协方差求解。

    入选资产在窗口内共同有效的交易日不足窗口一半（至少 20 天）时，退回等权并记 debug
    日志——新上市指数在回测早期常见，此时协方差不可估。
    """

    def allocate(
        self,
        config: PortfolioConfig,
        rankings: list[AssetRanking],
        timing: TimingSignal | None = None,
        context: EngineContext | None = None,
    ) -> dict[str, float]:
        """按等风险贡献分配。"""
        if not rankings:
            return {}
        total_exposure = resolve_target_exposure(config, timing)
        codes = [r.index_code for r in rankings]
        weights = None
        if context is not None and context.return_panel is not None:
            block = context.return_panel.window(context.trade_date, config.cov_window, codes)
            cov = covariance(block, max(20, config.cov_window // 2))
            weights = risk_parity_weights(cov) if cov is not None else None
        if weights is None:
            logger.debug("风险平价协方差不可估，退回等权: codes=%s", codes)
            return {code: round(total_exposure / len(codes), 4) for code in codes}
        return _scale_raw_weights(
            {code: float(w) for code, w in zip(codes, weights)}, total_exposure
        )


def build_allocator(method: str) -> WeightAllocator:
    """根据方法名构建权重分配器。

    Args:
        method: 分配方法，equal_weight / score_weight / winner_take_all / inverse_vol /
            risk_parity。

    Returns:
        权重分配器实例。

    Raises:
        ValueError: 未知的分配方法。
    """
    allocators: dict[str, WeightAllocator] = {
        "equal_weight": EqualWeightAllocator(),
        "score_weight": ScoreWeightAllocator(),
        "winner_take_all": WinnerTakeAllAllocator(),
        "inverse_vol": InverseVolAllocator(),
        "risk_parity": RiskParityAllocator(),
    }
    if method not in allocators:
        raise ValueError(f"未知的权重分配方法: {method}，可用: {list(allocators.keys())}")
    return allocators[method]
