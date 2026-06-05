"""评分模块：计算资产综合得分和择时得分。

内置 5 个变换函数，从旧的 timing.py / rotation.py 精确迁移。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import ScoreConfig, TimingConfig

logger = logging.getLogger(__name__)

# ── 变换函数注册表 ────────────────────────────────────────────────────────


def _invert_percentile(value: float) -> float:
    """百分位反转：百分位越低（越便宜）得分越高。"""
    return 100.0 - value


def _momentum_score(value: float) -> float:
    """收益率映射为动量得分（0-100）。

    精确复用旧 rotation.py 的分段线性映射逻辑。
    """
    if value > 15:
        return 95.0
    if value > 10:
        return 85.0
    if value > 5:
        return 70.0
    if value > 2:
        return 60.0
    if value > 0:
        return 50.0
    if value > -2:
        return 40.0
    if value > -5:
        return 30.0
    if value > -10:
        return 20.0
    return 10.0


def _volume_score(value: float) -> float:
    """量比映射为量能得分（0-100）。

    精确复用旧 timing.py 的分段线性映射逻辑。
    """
    if value < 0.3:
        return 10.0
    if value < 0.5:
        return 20.0
    if value < 0.8:
        return 35.0
    if value < 1.0:
        return 50.0
    if value < 1.3:
        return 70.0
    if value < 1.5:
        return 80.0
    if value < 2.0:
        return 85.0
    if value < 3.0:
        return 70.0
    return 50.0


def _trend_score(value: float) -> float:
    """价格相对 MA60 偏离度映射为趋势得分（0-100）。

    精确复用旧 timing.py 的线性映射逻辑。
    value 为偏离百分比：(price - ma60) / ma60 * 100。
    """
    if value <= -10:
        return 0.0
    if value >= 10:
        return 100.0
    return round(50 + value * 5, 1)


def _clamp_0_100(value: float) -> float:
    """通用裁剪：限制在 0-100 范围内。"""
    return max(0.0, min(100.0, value))


_TRANSFORM_REGISTRY: dict[str, Any] = {
    "invert_percentile": _invert_percentile,
    "momentum_score": _momentum_score,
    "volume_score": _volume_score,
    "trend_score": _trend_score,
    "clamp_0_100": _clamp_0_100,
}


def get_transform(name: str) -> Any:
    """获取变换函数。

    Args:
        name: 变换函数名称。

    Returns:
        变换函数。

    Raises:
        KeyError: 未知的变换函数名称。
    """
    if name not in _TRANSFORM_REGISTRY:
        raise KeyError(f"未知的变换函数: {name}，可用: {list(_TRANSFORM_REGISTRY.keys())}")
    return _TRANSFORM_REGISTRY[name]


# ── Protocol ──────────────────────────────────────────────────────────────


class ScoreCalculator(Protocol):
    """评分计算器协议。"""

    def calculate(self, config: ScoreConfig, context: EngineContext) -> dict[str, float]:
        """计算每资产综合得分。

        Args:
            config: 评分配置。
            context: 引擎上下文。

        Returns:
            key=etf_code, value=得分（0-100）。
        """
        ...

    def calculate_timing(
        self, config: TimingConfig, context: EngineContext
    ) -> tuple[float, str, dict[str, Any]]:
        """计算择时综合得分。

        Args:
            config: 择时配置。
            context: 引擎上下文。

        Returns:
            (composite_score, regime, factors_detail) 元组。
        """
        ...


# ── 默认实现 ──────────────────────────────────────────────────────────────


class DefaultScoreCalculator:
    """默认评分计算器。

    评分公式：score = Σ(transform(factor_value) × weight) / Σ(|weight|)
    仅对有值因子归一化权重，支持 missing_factor_strategy 控制缺失行为。
    """

    def calculate(self, config: ScoreConfig, context: EngineContext) -> dict[str, float]:
        """计算每资产综合得分。"""
        scores: dict[str, float] = {}
        for item in context.universe:
            code = item["etf_code"]
            score = self._score_single_asset(code, config, context)
            if score is not None:
                scores[code] = score
        return scores

    def calculate_timing(
        self, config: TimingConfig, context: EngineContext
    ) -> tuple[float, str, dict[str, Any]]:
        """计算择时综合得分。"""
        weighted_scores: list[tuple[float, float]] = []
        details: dict[str, Any] = {}

        for factor_id, weight in config.factors.items():
            raw_value = context.market_factors.get(factor_id)
            if raw_value is None:
                details[factor_id] = {"raw": None, "transformed": None, "status": "missing"}
                continue

            transform_name = config.transforms.get(factor_id)
            if transform_name:
                transform_fn = get_transform(transform_name)
                transformed = transform_fn(raw_value)
            else:
                transformed = raw_value

            weighted_scores.append((weight, transformed))
            details[factor_id] = {
                "raw": raw_value,
                "transformed": round(transformed, 1),
                "weight": weight,
            }

        if not weighted_scores:
            return 0.0, "neutral", {"reason": "所有因子数据缺失"}

        total_weight = sum(abs(w) for w, _ in weighted_scores)
        composite = sum(w * s for w, s in weighted_scores) / total_weight if total_weight > 0 else 0.0

        # 判定 regime
        thresholds = config.thresholds
        if composite >= thresholds.offensive:
            regime = "offensive"
        elif composite <= thresholds.defensive:
            regime = "defensive"
        else:
            regime = "neutral"

        details["composite_score"] = round(composite, 1)
        return round(composite, 1), regime, details

    def _score_single_asset(
        self, code: str, config: ScoreConfig, context: EngineContext
    ) -> float | None:
        """计算单个资产的综合得分。"""
        weighted_scores: list[tuple[float, float]] = []

        for factor_id, weight in config.factors.items():
            raw_value = context.asset_factors.get((code, factor_id))

            if raw_value is None:
                if config.missing_factor_strategy == "exclude":
                    return None
                if config.missing_factor_strategy == "zero":
                    raw_value = 0.0
                else:
                    # ignore: 跳过该因子，不参与加权
                    continue

            transform_name = config.transforms.get(factor_id)
            if transform_name:
                transform_fn = get_transform(transform_name)
                transformed = transform_fn(raw_value)
            else:
                transformed = raw_value

            weighted_scores.append((weight, transformed))

        if not weighted_scores:
            return None

        total_weight = sum(abs(w) for w, _ in weighted_scores)
        if total_weight == 0:
            return 0.0

        score = sum(w * s for w, s in weighted_scores) / total_weight
        return round(max(0.0, min(100.0, score)), 1)
