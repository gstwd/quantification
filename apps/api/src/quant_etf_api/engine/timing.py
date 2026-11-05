"""择时评估：原始 regime → 状态层 → 有效择时信号（实时、回测与研究台共用）。

``ScoreCalculator.calculate_timing`` 只给出当日的原始判定（综合分过阈值或规则组）；
本模块在其上施加 ``timing.state`` 状态层（滞回、无信号维持、确认、最短持有），
输出实际用于决定仓位的 :class:`TimingSignal` 与当日结束时的状态。

状态由调用方逐日传递：回测主循环与研究台按交易日顺序推进；实时路径没有持久化
状态，由 ``ContextBuilder`` 回放最近 :data:`TIMING_STATE_REPLAY_DAYS` 个交易日重建，
所以实时与回测走的是同一条状态转移实现。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Protocol

from quant_etf_api.domain.strategies.models import TimingSignal
from quant_etf_api.domain.strategies.timing_state import (
    TimingState,
    resolve_candidate_regime,
    resolve_effective_tier,
    resolve_tier_exposure,
    step_timing_state,
)
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import TimingConfig
from quant_etf_api.engine.filter import evaluate_rule_group

# 实时路径重建择时状态时回放的交易日数（不含当日）。状态记忆超过该长度时，实时
# 与"从回测起点一路推进"的结果可能不同；常用参数（确认 ≤60、持有 ≤250）下足够。
TIMING_STATE_REPLAY_DAYS = 250

_REGIME_LABELS: dict[str, str] = {"offensive": "进攻", "defensive": "防守", "neutral": "观望"}


class TimingCalculator(Protocol):
    """原始择时判定的计算协议（``ScoreCalculator`` 的择时部分）。"""

    def calculate_timing(
        self, config: TimingConfig, context: EngineContext
    ) -> tuple[float, str, dict[str, Any]]:
        """计算当日原始择时判定。

        Args:
            config: 择时配置。
            context: 引擎上下文。

        Returns:
            (综合分, 原始 regime, 明细) 元组。
        """
        ...


def evaluate_timing(
    calculator: TimingCalculator,
    config: TimingConfig,
    context: EngineContext,
) -> tuple[TimingSignal, TimingState]:
    """计算当日有效择时信号，并推进状态层。

    ``context.timing_state`` 为上一决策日结束时的状态（None 表示无历史）；
    ``on_degraded=hold_previous`` 沿用的"上一 regime"同样取自它，没有状态时
    退回 ``context.previous_timing_regime``。

    降级日（择时因子全部缺失）的回退 regime 是显式策略，不受确认天数与最短
    持有期约束：直接成为有效 regime 并重置状态。

    配置了多档仓位（``tiers``）时，有效档位跟随有效 regime（见
    :func:`resolve_effective_tier`）：状态层接受当日判定时用当日命中档位，沿用前
    regime 时沿用上一档位；有效档位写入状态、信号的 ``tier`` / ``exposure`` 与明细
    ``tier``。未配置档位时信号与明细不含档位字段。

    Args:
        calculator: 原始择时判定计算器。
        config: 择时配置。
        context: 引擎上下文。

    Returns:
        (有效择时信号, 当日结束时的状态)。
    """
    previous = context.timing_state
    previous_regime = previous.regime if previous is not None else context.previous_timing_regime
    if previous_regime != context.previous_timing_regime:
        context = replace(context, previous_timing_regime=previous_regime)

    composite, raw_regime, details = calculator.calculate_timing(config, context)
    degraded = bool(details.get("degraded"))
    state_cfg = config.state

    if degraded:
        regime = raw_regime
        if previous is not None and previous.regime == regime:
            new_state = TimingState(regime=regime, held_days=previous.held_days + 1)
        else:
            new_state = TimingState(regime=regime)
    else:
        candidate = resolve_candidate_regime(
            raw_regime,
            composite if config.combine == "weighted" else None,
            previous_regime,
            hysteresis=state_cfg.hysteresis,
            hold_on_neutral=state_cfg.hold_on_neutral,
            offensive_threshold=config.thresholds.offensive,
            defensive_threshold=config.thresholds.defensive,
        )
        new_state = step_timing_state(
            previous,
            candidate,
            confirm_days=state_cfg.confirm_days,
            min_hold_days=state_cfg.min_hold_days,
        )
        regime = new_state.regime

    tier: int | None = None
    tier_exposure: float | None = None
    if config.tiers:
        raw_tier = details.get("matched_tier")
        tier, tier_source = resolve_effective_tier(
            regime=regime,
            raw_regime=raw_regime,
            raw_tier=raw_tier if isinstance(raw_tier, int) else None,
            previous=previous,
            degraded=degraded,
            hold_previous_on_degraded=config.on_degraded == "hold_previous",
        )
        # 配置变更后回放出的旧序号可能越界：按无档位处理
        if tier is not None and not 0 <= tier < len(config.tiers):
            tier, tier_source = None, "none"
        if tier is not None:
            tier_cfg = config.tiers[tier]
            tier_exposure = resolve_tier_exposure(
                source=tier_source,
                exposure=tier_cfg.exposure,
                hold=tier_cfg.hold,
                exposure_delta=tier_cfg.exposure_delta,
                min_exposure=tier_cfg.min_exposure,
                max_exposure=tier_cfg.max_exposure,
                previous_exposure=previous.exposure if previous is not None else None,
                base_exposure=config.tier_base_exposure,
                max_step=state_cfg.max_exposure_step,
            )
        new_state = replace(new_state, tier=tier, exposure=tier_exposure)
        details = dict(details)
        details["tier"] = _tier_detail(config, tier, tier_source, raw_tier, tier_exposure)

    cap, cap_label = None, None
    if config.exposure_caps:
        cap, cap_label, cap_details = _match_exposure_caps(config, context)
        details = dict(details)
        details["exposure_caps"] = cap_details

    if not state_cfg.is_identity:
        details = dict(details)
        details["raw_regime"] = raw_regime
        details["state"] = new_state.to_detail()

    tier_config = config.tiers[tier] if tier is not None else None
    return (
        TimingSignal(
            regime=regime,
            confidence=_confidence(config, composite, regime, raw_regime, degraded),
            label=_REGIME_LABELS.get(regime, "观望"),
            factors=details,
            tier=tier,
            tier_label=tier_config.display_label(tier) if tier_config is not None else None,
            exposure=tier_exposure,
            exposure_cap=cap,
            exposure_cap_label=cap_label,
        ),
        new_state,
    )


def _match_exposure_caps(
    config: TimingConfig, context: EngineContext
) -> tuple[float | None, str | None, list[dict[str, Any]]]:
    """评估条件仓位上限：命中的上限取最小值。

    Args:
        config: 择时配置（已配置 exposure_caps）。
        context: 引擎上下文（市场级因子值）。

    Returns:
        (生效上限, 展示名, 逐条明细)；都未命中时上限为 None。
    """
    cap: float | None = None
    label: str | None = None
    details: list[dict[str, Any]] = []
    for index, item in enumerate(config.exposure_caps):
        hit = evaluate_rule_group(item.as_rule_group(), context.market_factors.get)
        details.append(
            {
                "index": index,
                "label": item.display_label(index),
                "max_exposure": item.max_exposure,
                "matched": hit,
            }
        )
        if hit and (cap is None or item.max_exposure < cap):
            cap, label = item.max_exposure, item.display_label(index)
    return cap, label, details


def _tier_detail(
    config: TimingConfig,
    tier: int | None,
    source: str,
    matched: Any,
    exposure: float | None = None,
) -> dict[str, Any]:
    """构造择时明细中的有效档位字段。

    Args:
        config: 择时配置（已配置 tiers）。
        tier: 有效档位序号；None 表示按 regime 仓位表。
        source: 有效档位来源（matched / held / none）。
        matched: 当日原始命中的档位序号（None 表示未命中）。
        exposure: 当日有效档位的目标仓位（动态档位按上一仓位算出）。

    Returns:
        含 index / label / exposure / source / matched_index 的字典；动态档位另含 mode。
    """
    tier_config = config.tiers[tier] if tier is not None else None
    detail: dict[str, Any] = {
        "index": tier,
        "label": tier_config.display_label(tier) if tier_config is not None else None,
        "exposure": exposure,
        "source": source,
        "matched_index": matched if isinstance(matched, int) else None,
    }
    if tier_config is not None and tier_config.is_dynamic:
        # 动态档位额外给出仓位方式（维持 / 增减），固定档位不输出该字段
        detail["mode"] = tier_config.exposure_text()
    return detail


def _confidence(
    config: TimingConfig, composite: float, regime: str, raw_regime: str, degraded: bool
) -> float:
    """计算展示用确信度（0-100，不参与任何决策）。

    Args:
        config: 择时配置。
        composite: 当日综合分。
        regime: 有效 regime。
        raw_regime: 原始 regime。
        degraded: 是否降级日。

    Returns:
        确信度（1 位小数）。
    """
    if degraded:
        # 降级回退不是由综合分推出的判断，确信度取下限（neutral 回退与历史口径一致）
        return 20.0
    if config.combine == "rules":
        # 规则模式没有连续综合分：当日规则命中给 60，由状态层沿用的给 40
        return 60.0 if regime == raw_regime and regime != "neutral" else 40.0
    thresholds = config.thresholds
    if regime == "offensive":
        value = min(100.0, (composite - thresholds.offensive) * 2 + 60)
    elif regime == "defensive":
        value = min(100.0, (thresholds.defensive - composite) * 2 + 60)
    else:
        value = 60 - abs(composite - 50)
    return round(max(20.0, value), 1)
