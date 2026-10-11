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
    """默认评分计算器（absolute 模式）。

    评分公式：score = Σ(|w| × d) / Σ|w|，其中 s = transform(factor_value)，
    d = s（w ≥ 0）或 100 − s（w < 0）。

    absolute 模式的因子得分处于 0-100 空间，负权重表达"反向"：直接用 w × s 会把
    综合分整体压向 0 再被截断，而不是让低值因子得高分。仅对参与加权的因子归一化
    权重，支持 missing_factor_strategy 控制缺失行为（zero=该因子按方向记 0 分，
    即"按最差处理"）。
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
                # contribution 已在 _score_single_asset 中按方向化得分回填
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
        """计算择时综合得分。

        与 absolute 评分同一口径：负权重按 100 − 得分参与加权。部分因子缺失时
        在明细中列出 ``missing_factors``；全部缺失时回退为 neutral 并标记
        ``degraded=True``，由服务层转成结构化告警，避免静默按中性仓位运行。

        Args:
            config: 择时配置。
            context: 引擎上下文。

        Returns:
            (composite_score, regime, factors_detail) 元组。
        """
        weighted_scores: list[tuple[float, float]] = []
        details: dict[str, Any] = {}
        missing_factors: list[str] = []

        for factor_id, weight in config.factors.items():
            raw_value = context.market_factors.get(factor_id)
            if raw_value is None:
                details[factor_id] = {"raw": None, "transformed": None, "status": "missing"}
                missing_factors.append(factor_id)
                continue

            transform_name = config.transforms.get(factor_id)
            if transform_name:
                transform_fn = get_transform(transform_name)
                transformed = transform_fn(raw_value)
            else:
                transformed = raw_value

            weighted_scores.append((abs(weight), _directional(transformed, weight)))
            details[factor_id] = {
                "raw": raw_value,
                "transformed": round(transformed, 1),
                "weight": weight,
            }

        if not weighted_scores:
            return (
                0.0,
                "neutral",
                {
                    "reason": "所有因子数据缺失",
                    "degraded": True,
                    "missing_factors": missing_factors,
                },
            )
        if missing_factors:
            details["missing_factors"] = missing_factors

        total_weight = sum(w for w, _ in weighted_scores)
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
        # (|权重|, 方向化得分, 对应明细) 三元组；明细为 None 表示未收集调试数据
        weighted_scores: list[tuple[float, float, FactorScoreBreakdown | None]] = []

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
                    # 按最差处理：方向化得分记 0（不能先把原值置 0 再变换——
                    # 例如 invert_percentile(0)=100，缺数据的资产反而拿到满分）
                    entry = None
                    if breakdown is not None:
                        entry = FactorScoreBreakdown(
                            factor_id=factor_id,
                            raw_value=None,
                            transformed_value=None,
                            weight=weight,
                            contribution=0.0,
                            status="missing_zero",
                        )
                        breakdown.append(entry)
                    weighted_scores.append((abs(weight), 0.0, entry))
                    continue
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

            transform_name = config.transforms.get(factor_id)
            if transform_name:
                transform_fn = get_transform(transform_name)
                transformed = transform_fn(raw_value)
            else:
                transformed = raw_value

            entry = None
            if breakdown is not None:
                entry = FactorScoreBreakdown(
                    factor_id=factor_id,
                    raw_value=raw_value,
                    transformed_value=round(transformed, 1)
                    if isinstance(transformed, float)
                    else transformed,
                    weight=weight,
                    contribution=0.0,  # 合成后统一回填
                    status="ok",
                )
                breakdown.append(entry)
            weighted_scores.append((abs(weight), _directional(transformed, weight), entry))

        if not weighted_scores:
            return None

        total_weight = sum(w for w, _, _ in weighted_scores)
        if total_weight == 0:
            return 0.0

        for w, directional, entry in weighted_scores:
            if entry is not None:
                entry.contribution = round(w * directional / total_weight, 2)
        score = sum(w * d for w, d, _ in weighted_scores) / total_weight
        return round(max(0.0, min(100.0, score)), 1)


def _directional(value: float, weight: float) -> float:
    """把 0-100 空间的因子得分按权重方向转换（负权重取 100 − 值）。

    Args:
        value: 变换后的因子得分。
        weight: 因子权重。

    Returns:
        方向化后的得分：正权重原样返回，负权重返回 100 − value。
    """
    return value if weight >= 0 else 100.0 - value


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
        # zero 策略下缺失的因子：不参与横截面标准化，标准化后赋方向最差值
        asset_worst: dict[str, set[str]] = {}
        asset_breakdowns: dict[str, list[FactorScoreBreakdown]] = {}
        names: dict[str, str] = {}
        for item in context.universe:
            code = item["index_code"]
            name_cn = item.get("name_cn", code)
            names[code] = name_cn
            values: dict[str, float] = {}
            worst: set[str] = set()
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
                    # 按最差处理：不把假 0 值混入横截面分布（会扭曲均值/标准差），
                    # 标准化后再赋予该因子在横截面上方向最差的值
                    worst.add(factor_id)
                    breakdown.append(
                        FactorScoreBreakdown(
                            factor_id=factor_id,
                            raw_value=None,
                            transformed_value=None,
                            weight=weight,
                            contribution=0.0,
                            status="missing_zero",
                        )
                    )
                    continue
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
            if not any(abs(config.factors[factor_id]) > 0 for factor_id in (*values, *worst)):
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
            asset_worst[code] = worst
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
                normalized = normalize(column)
                present = [v for v in normalized.values() if v is not None]
                weight = config.factors[factor_id]
                # zero 策略缺失：比该因子横截面上方向最差的资产再差一档（正权重取
                # 最小值再减一档、负权重取最大值再加一档；rank 模式一档=一个名次，
                # zscore 模式一档=一个标准差），保证缺数据者严格排在有数据者之后；
                # 无任何有值资产时该因子对这些资产视为缺失（忽略）
                if present:
                    step = 100.0 / len(present) if mode == "rank" else 1.0
                    worst_value = min(present) - step if weight >= 0 else max(present) + step
                    for code, worst_refs in asset_worst.items():
                        if factor_id in worst_refs:
                            normalized[code] = worst_value
                normalized_by_factor[factor_id] = normalized

        # 第三步：按权重合成综合值（只计入该资产可用的因子）
        raw_scores: dict[str, float] = {}
        for code, values in asset_values.items():
            weighted_sum = 0.0
            total_weight = 0.0
            for factor_id in (*values, *asset_worst.get(code, set())):
                weight = config.factors[factor_id]
                standardized = normalized_by_factor.get(factor_id, {}).get(code)
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
                    if standardized is None or (
                        entry.factor_id not in values
                        and entry.factor_id not in asset_worst.get(code, set())
                    ):
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
