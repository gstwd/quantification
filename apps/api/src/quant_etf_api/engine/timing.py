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
    step_timing_state,
)
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import TimingConfig

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

    if not state_cfg.is_identity:
        details = dict(details)
        details["raw_regime"] = raw_regime
        details["state"] = new_state.to_detail()

    return (
        TimingSignal(
            regime=regime,
            confidence=_confidence(config, composite, regime, raw_regime, degraded),
            label=_REGIME_LABELS.get(regime, "观望"),
            factors=details,
        ),
        new_state,
    )


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
