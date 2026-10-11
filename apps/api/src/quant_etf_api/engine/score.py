"""评分模块：计算资产综合得分和择时得分。

变换函数已迁移至 engine/transforms.py（独立注册表），
本模块保留 _TRANSFORM_REGISTRY / get_transform 的导入以兼容既有引用。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import ScoreConfig, TimingConfig
from quant_etf_api.engine.pipeline_detail import (
    AssetScoreDetail,
    FactorScoreBreakdown,
)

logger = logging.getLogger(__name__)

# 兼容转发：变换注册表与查询函数来自 engine.transforms（独立扩展点）
from quant_etf_api.engine.transforms import (  # noqa: F401, E402
    _TRANSFORM_REGISTRY,
    get_transform,
)


# ── Protocol ──────────────────────────────────────────────────────────────


class ScoreCalculator(Protocol):
    """评分计算器协议。"""

    def calculate(self, config: ScoreConfig, context: EngineContext) -> dict[str, float]:
        """计算每资产综合得分。

        Args:
            config: 评分配置。
            context: 引擎上下文。

        Returns:
            key=index_code, value=得分（0-100）。
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

    def calculate(
        self,
        config: ScoreConfig,
        context: EngineContext,
        debug: list[AssetScoreDetail] | None = None,
    ) -> dict[str, float]:
        """计算每资产综合得分。

        Args:
            config: 评分配置。
            context: 引擎上下文。
            debug: 可选的调试收集列表，传入时填充每资产的评分明细。

        Returns:
            key=index_code, value=得分（0-100）。
        """
        scores: dict[str, float] = {}
        for item in context.universe:
            code = item["index_code"]
            name_cn = item.get("name_cn", code)
            breakdown: list[FactorScoreBreakdown] = []
            score = self._score_single_asset(code, config, context, breakdown)
            if score is not None:
                scores[code] = score
                logger.debug(
                    "[pipeline] 评分明细: %s score=%s factors=%s",
                    code,
                    round(score, 2),
                    {b.factor_id: b.status for b in breakdown},
                )
            else:
                logger.debug(
                    "[pipeline] 评分明细: %s score=none（资产被排除）factors=%s",
                    code,
                    {b.factor_id: b.status for b in breakdown},
                )
            if debug is not None:
                # 回填 contribution
                total_abs = sum(abs(b.weight) for b in breakdown if b.status == "ok")
                for b in breakdown:
                    if b.status == "ok" and total_abs > 0:
                        b.contribution = round((b.transformed_value or 0) * b.weight / total_abs, 2)
                debug.append(
                    AssetScoreDetail(
                        index_code=code,
                        name_cn=name_cn,
                        raw_score=score,
                        final_score=score,
                        factors=breakdown,
                        excluded=score is None,
                        exclude_reason="因子数据缺失" if score is None else "",
                    )
                )
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
        composite = (
            sum(w * s for w, s in weighted_scores) / total_weight if total_weight > 0 else 0.0
        )

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
        self,
        code: str,
        config: ScoreConfig,
        context: EngineContext,
        breakdown: list[FactorScoreBreakdown] | None = None,
    ) -> float | None:
        """计算单个资产的综合得分。

        Args:
            code: 资产代码。
            config: 评分配置。
            context: 引擎上下文。
            breakdown: 可选的调试收集列表，传入时记录每个因子的计算明细。

        Returns:
            综合得分（0-100），因子缺失且策略为 exclude 时返回 None。
        """
        weighted_scores: list[tuple[float, float]] = []

        for factor_id, weight in config.factors.items():
            raw_value = context.asset_factors.get((code, factor_id))

            if raw_value is None:
                if config.missing_factor_strategy == "exclude":
                    if breakdown is not None:
                        breakdown.append(
                            FactorScoreBreakdown(
                                factor_id=factor_id,
                                raw_value=None,
                                transformed_value=None,
                                weight=weight,
                                contribution=0.0,
                                status="missing_excluded",
                            )
                        )
                    return None
                if config.missing_factor_strategy == "zero":
                    raw_value = 0.0
                    status = "missing_zero"
                else:
                    # ignore: 跳过该因子，不参与加权
                    if breakdown is not None:
                        breakdown.append(
                            FactorScoreBreakdown(
                                factor_id=factor_id,
                                raw_value=None,
                                transformed_value=None,
                                weight=weight,
                                contribution=0.0,
                                status="missing_ignored",
                            )
                        )
                    continue
            else:
                status = "ok"

            transform_name = config.transforms.get(factor_id)
            if transform_name:
                transform_fn = get_transform(transform_name)
                transformed = transform_fn(raw_value)
            else:
                transformed = raw_value

            weighted_scores.append((weight, transformed))

            if breakdown is not None:
                breakdown.append(
                    FactorScoreBreakdown(
                        factor_id=factor_id,
                        raw_value=raw_value,
                        transformed_value=round(transformed, 1)
                        if isinstance(transformed, float)
                        else transformed,
                        weight=weight,
                        contribution=0.0,  # 最终统一回填
                        status=status,
                    )
                )

        if not weighted_scores:
            return None

        total_weight = sum(abs(w) for w, _ in weighted_scores)
        if total_weight == 0:
            return 0.0

        score = sum(w * s for w, s in weighted_scores) / total_weight
        return round(max(0.0, min(100.0, score)), 1)


_default_scorer = DefaultScoreCalculator()


class CrossSectionScorer:
    """横截面评分器：逐因子横截面标准化后加权合成，再在资产间统一评分。

    支持两种模式：
    - rank：每个因子先转为横截面百分位排名（并列取平均秩），加权合成后
      再对综合值做百分位排名（0-100）；
    - zscore：每个因子先做横截面 Z-Score，加权合成后再对综合值做 Z-Score，
      映射到 0-100（均值 50，每个标准差 10 分）。

    逐因子标准化消除了不同因子的量纲差异：配置权重即各因子在综合分中的
    相对重要性，不再被因子自身的横截面离散度放大或压缩。

    与 DefaultScoreCalculator 的区别：
    DefaultScoreCalculator 每资产独立评分（加权平均自身因子值），
    CrossSectionScorer 在横截面上比较所有资产后统一评分，
    适用于轮动策略、相对强弱策略等需要资产间比较的场景。
    """

    def calculate(
        self,
        config: "ScoreConfig",
        context: "EngineContext",
        debug: list[AssetScoreDetail] | None = None,
    ) -> dict[str, float]:
        """横截面评分计算。

        Args:
            config: 评分配置，需设置 scoring_mode。
            context: 引擎上下文。
            debug: 可选的调试收集列表，传入时填充每资产的评分明细
                和横截面统计信息。

        Returns:
            key=index_code, value=得分（0-100）。
        """
        from quant_etf_api.factors.normalization import normalize_rank, normalize_zscore

        mode = config.scoring_mode
        if mode == "rank":
            normalize = normalize_rank
        elif mode == "zscore":
            normalize = normalize_zscore
        else:
            raise ValueError(
                f"CrossSectionScorer 不支持 scoring_mode='{mode}'，"
                f"absolute 模式应使用 DefaultScoreCalculator"
            )

        # 第一步：逐资产收集变换后的因子值，处理缺失策略
        asset_values: dict[str, dict[str, float]] = {}
        asset_breakdowns: dict[str, list[FactorScoreBreakdown]] = {}
        names: dict[str, str] = {}
        for item in context.universe:
            code = item["index_code"]
            name_cn = item.get("name_cn", code)
            names[code] = name_cn
            values: dict[str, float] = {}
            breakdown: list[FactorScoreBreakdown] = []
            skip_asset = False
            for factor_id, weight in config.factors.items():
                raw_value = context.asset_factors.get((code, factor_id))
                if raw_value is None:
                    if config.missing_factor_strategy == "exclude":
                        skip_asset = True
                        breakdown.append(
                            FactorScoreBreakdown(
                                factor_id=factor_id,
                                raw_value=None,
                                transformed_value=None,
                                weight=weight,
                                contribution=0.0,
                                status="missing_excluded",
                            )
                        )
                        break
                    if config.missing_factor_strategy != "zero":
                        breakdown.append(
                            FactorScoreBreakdown(
                                factor_id=factor_id,
                                raw_value=None,
                                transformed_value=None,
                                weight=weight,
                                contribution=0.0,
                                status="missing_ignored",
                            )
                        )
                        continue
                    raw_value = 0.0
                    status = "missing_zero"
                else:
                    status = "ok"

                transform_name = config.transforms.get(factor_id)
                transformed = (
                    get_transform(transform_name)(raw_value) if transform_name else raw_value
                )
                values[factor_id] = transformed
                breakdown.append(
                    FactorScoreBreakdown(
                        factor_id=factor_id,
                        raw_value=raw_value,
                        transformed_value=None,
                        weight=weight,
                        contribution=0.0,
                        status=status,
                    )
                )

            if skip_asset:
                if debug is not None:
                    debug.append(
                        AssetScoreDetail(
                            index_code=code,
                            name_cn=name_cn,
                            raw_score=None,
                            final_score=None,
                            factors=breakdown,
                            excluded=True,
                            exclude_reason="因子数据缺失（exclude 策略）",
                        )
                    )
                continue
            if not any(abs(config.factors[factor_id]) > 0 for factor_id in values):
                # 所有因子缺失（ignore 策略）时与 absolute 模式一致：视为排除，
                # 避免假 0 分污染横截面排名/标准化分布，防止被 bottom_n 反向选入
                if debug is not None:
                    debug.append(
                        AssetScoreDetail(
                            index_code=code,
                            name_cn=name_cn,
                            raw_score=None,
                            final_score=None,
                            factors=breakdown,
                            excluded=True,
                            exclude_reason="因子数据全部缺失",
                        )
                    )
                continue
            asset_values[code] = values
            asset_breakdowns[code] = breakdown

        if not asset_values:
            return {}

        # 第二步：逐因子在参与评分的资产间做横截面标准化，消除量纲差异
        normalized_by_factor: dict[str, dict[str, float | None]] = {}
        for factor_id in config.factors:
            column = {
                code: values[factor_id]
                for code, values in asset_values.items()
                if factor_id in values
            }
            if column:
                normalized_by_factor[factor_id] = normalize(column)

        # 第三步：按权重合成综合值（只计入该资产可用的因子）
        raw_scores: dict[str, float] = {}
        for code, values in asset_values.items():
            weighted_sum = 0.0
            total_weight = 0.0
            for factor_id in values:
                weight = config.factors[factor_id]
                standardized = normalized_by_factor[factor_id].get(code)
                if standardized is None:
                    continue
                weighted_sum += standardized * weight
                total_weight += abs(weight)
            if total_weight <= 0:
                continue
            raw_scores[code] = weighted_sum / total_weight
            if debug is not None:
                for entry in asset_breakdowns[code]:
                    standardized = normalized_by_factor.get(entry.factor_id, {}).get(code)
                    if entry.factor_id not in values or standardized is None:
                        continue
                    entry.transformed_value = round(standardized, 2)
                    entry.contribution = round(standardized * entry.weight / total_weight, 2)

        if not raw_scores:
            return {}

        # 第四步：综合值的横截面变换（映射到 0-100）
        if mode == "rank":
            normalized = normalize_rank(raw_scores)
        else:
            normalized = {
                code: (round(max(0.0, min(100.0, 50.0 + z * 10.0)), 1) if z is not None else 50.0)
                for code, z in normalize_zscore(raw_scores).items()
            }

        # 第五步：填充调试数据
        if debug is not None:
            for code in raw_scores:
                debug.append(
                    AssetScoreDetail(
                        index_code=code,
                        name_cn=names.get(code, code),
                        raw_score=round(raw_scores[code], 2),
                        final_score=normalized.get(code),
                        factors=asset_breakdowns.get(code, []),
                        excluded=False,
                    )
                )

        return normalized

    def calculate_timing(
        self,
        config: "TimingConfig",
        context: "EngineContext",
    ) -> tuple[float, str, dict]:
        """横截面评分器的择时计算（委托给 DefaultScoreCalculator 单例）。"""
        return _default_scorer.calculate_timing(config, context)
