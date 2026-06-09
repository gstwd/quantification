"""策略引擎编排器：串联 Score → Filter → Rank → Portfolio → Risk 管线。

StrategyEngine 是策略执行的核心入口，接收 StrategyConfig 和 EngineContext，
按管线顺序调用各模块，输出统一的 EngineResult。
"""

from __future__ import annotations

import logging
from typing import Any

from quant_etf_api.domain.common.signal_level import determine_signal_level
from quant_etf_api.domain.strategies.models import (
    AssetRanking,
    StrategyResult,
    TimingSignal,
)
from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.filter import DefaultFilterEngine, FilterEngine
from quant_etf_api.engine.portfolio import build_allocator
from quant_etf_api.engine.rank import DefaultRankEngine, RankEngine
from quant_etf_api.engine.risk import DefaultRiskManager, RiskManager
from quant_etf_api.engine.score import CrossSectionScorer, DefaultScoreCalculator, ScoreCalculator

logger = logging.getLogger(__name__)


class StrategyEngine:
    """策略引擎编排器。

    执行管线：Timing → Score → Filter → Rank → Portfolio → Risk → Output。
    无 portfolio 配置时为信号模式（只输出得分/排名），
    有 portfolio 配置时为配置模式（输出仓位）。
    """

    def __init__(
        self,
        score_calculator: ScoreCalculator | None = None,
        filter_engine: FilterEngine | None = None,
        rank_engine: RankEngine | None = None,
        risk_manager: RiskManager | None = None,
    ) -> None:
        """初始化策略引擎。

        Args:
            score_calculator: 评分计算器，默认使用 DefaultScoreCalculator。
            filter_engine: 过滤引擎，默认使用 DefaultFilterEngine。
            rank_engine: 排名引擎，默认使用 DefaultRankEngine。
            risk_manager: 风控管理器，默认使用 DefaultRiskManager。
        """
        self._score = score_calculator or DefaultScoreCalculator()
        self._filter = filter_engine or DefaultFilterEngine()
        self._rank = rank_engine or DefaultRankEngine()
        self._risk = risk_manager or DefaultRiskManager()

    def run(self, config: StrategyConfig, context: EngineContext) -> EngineResult:
        """执行策略管线。

        Args:
            config: 策略配置。
            context: 引擎上下文。

        Returns:
            统一的引擎执行结果。
        """
        # 1. 择时评估（可选）
        timing = None
        if config.timing:
            timing = self._run_timing(config, context)

        # 1.5 regime 条件化配置覆盖
        effective = self._resolve_regime_config(config, timing)

        # 2. 资产评分（根据 scoring_mode 选择评分器）
        if effective.score.scoring_mode != "absolute":
            scores = CrossSectionScorer().calculate(effective.score, context)
        else:
            scores = self._score.calculate(effective.score, context)

        # 3. 过滤（可选）
        if effective.filters:
            scores = self._filter.filter(effective.filters, scores, context)

        # 4. 排名
        rankings = self._rank.rank(effective.rank, scores, context)

        # 5. 仓位分配（可选，无 portfolio 则为信号模式）
        positions: dict[str, float] = {}
        total_exposure = 0.0
        cash_ratio = 1.0
        if effective.portfolio:
            allocator = build_allocator(effective.portfolio.method)
            positions = allocator.allocate(effective.portfolio, rankings, timing)

            # 6. 风控裁剪（可选）
            if effective.risk:
                positions = self._risk.apply_constraints(effective.risk, positions)

            total_exposure = round(sum(positions.values()), 4)
            cash_ratio = round(1.0 - total_exposure, 4)

        # 7. 构建兼容旧接口的 StrategyResult 列表
        strategy_results = self._build_strategy_results(
            effective, context, timing, scores, rankings, positions, total_exposure, cash_ratio
        )

        return EngineResult(
            trade_date=context.trade_date,
            strategy_id=config.strategy_id,
            timing=timing,
            scores=scores,
            rankings=rankings,
            positions=positions,
            total_exposure=total_exposure,
            cash_ratio=cash_ratio,
            strategy_results=strategy_results,
        )

    def _run_timing(self, config: StrategyConfig, context: EngineContext) -> TimingSignal:
        """运行择时评估，返回 TimingSignal。"""
        composite_score, regime, factors = self._score.calculate_timing(config.timing, context)

        # 计算确信度
        thresholds = config.timing.thresholds
        if regime == "offensive":
            confidence = min(100.0, (composite_score - thresholds.offensive) * 2 + 60)
        elif regime == "defensive":
            confidence = min(100.0, (thresholds.defensive - composite_score) * 2 + 60)
        else:
            confidence = max(20.0, 60 - abs(composite_score - 50))

        label_map = {"offensive": "进攻", "defensive": "防守", "neutral": "观望"}

        return TimingSignal(
            regime=regime,
            confidence=round(confidence, 1),
            label=label_map.get(regime, "观望"),
            factors=factors,
        )

    def _resolve_regime_config(
        self, config: StrategyConfig, timing: TimingSignal | None
    ) -> StrategyConfig:
        """根据 timing regime 解析条件化策略配置。

        如果 config.regime_rules 非空且 timing 有 regime，
        从 regime_rules[regime] 中取覆盖值，合并到 config 副本中。
        未覆盖的字段保持原 config 值。

        Args:
            config: 原始策略配置。
            timing: 择时信号，None 表示无择时。

        Returns:
            合并后的有效策略配置（可能是原 config 或其副本）。
        """
        if not config.regime_rules or timing is None:
            return config

        rule = config.regime_rules.get(timing.regime)
        if rule is None:
            return config

        # 构建覆盖字典，只包含非 None 的字段
        overrides: dict[str, Any] = {}
        if rule.score is not None:
            overrides["score"] = rule.score
        if rule.filters is not None:
            overrides["filters"] = rule.filters
        if rule.rank is not None:
            overrides["rank"] = rule.rank
        if rule.portfolio is not None:
            overrides["portfolio"] = rule.portfolio

        if not overrides:
            return config

        # 创建 config 副本并应用覆盖
        merged = config.model_copy(update=overrides)
        logger.debug(
            "regime 配置覆盖: regime=%s, 覆盖字段=%s",
            timing.regime,
            list(overrides.keys()),
        )
        return merged

    def _build_strategy_results(
        self,
        config: StrategyConfig,
        context: EngineContext,
        timing: TimingSignal | None,
        scores: dict[str, float],
        rankings: list[AssetRanking],
        positions: dict[str, float],
        total_exposure: float,
        cash_ratio: float,
    ) -> list[StrategyResult]:
        """构建兼容旧接口的 StrategyResult 列表。"""
        rank_map = {r.etf_code: r for r in rankings}
        results: list[StrategyResult] = []

        for item in context.universe:
            code = item["etf_code"]
            score = scores.get(code, 0.0)
            ranking = rank_map.get(code)
            target_weight = positions.get(code, 0.0)

            # 统一信号等级判定
            level, label = determine_signal_level(
                score=score,
                target_weight=target_weight,
                has_positions=bool(positions),
                timing_regime=timing.regime if timing else None,
                scoring_mode=config.score.scoring_mode,
            )

            # 构建因子值列表（仅含 config.score.factors 中定义的真实因子，
            # timing_regime 和 target_weight 已在 payload 中记录，不重复写入因子值表）
            factor_values = []
            for factor_id in config.score.factors:
                raw = context.asset_factors.get((code, factor_id))
                factor_values.append({"factor_id": factor_id, "value": raw})

            # 构建 payload
            payload: dict[str, Any] = {
                "target_weight": target_weight,
                "total_exposure": total_exposure,
                "cash_ratio": cash_ratio,
            }
            if timing:
                payload["timing_regime"] = timing.regime
                payload["timing_label"] = timing.label
                payload["timing_confidence"] = timing.confidence
                payload["plan_reasoning"] = (
                    f"择时：{timing.label}（确信度 {timing.confidence:.0f}%），"
                    f"目标仓位 {total_exposure:.0%}"
                )
            if ranking:
                payload["momentum_rank"] = ranking.momentum_rank
                payload["valuation_rank"] = ranking.valuation_rank
                payload["category"] = ranking.category

            results.append(
                StrategyResult(
                    trade_date=context.trade_date,
                    etf_code=code,
                    strategy_id=config.strategy_id,
                    signal_score=round(score, 1),
                    signal_level=level,
                    signal_label=label,
                    factor_values=factor_values,
                    payload=payload,
                    tags=[ranking.category if ranking else ""],
                )
            )

        return results
