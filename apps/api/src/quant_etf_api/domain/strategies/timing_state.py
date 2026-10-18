"""择时状态层（纯领域规则，实时与回测共用）。

原始 regime（综合分过阈值或规则组判定）每日独立给出，研报常见的择时范式却是
有状态的：滞回带避免在阈值附近来回翻转、"无信号时维持前值"、新 regime 连续
N 日确认后才切换、切换后至少持有 N 日。本模块把这些规则收敛为单步状态转移：

1. 滞回（仅加权模式有综合分时生效）：上一有效 regime 为进攻时，综合分只要不低于
   ``进攻阈值 − hysteresis`` 且未落入防守区就继续进攻；防守对称；
2. 无信号维持（``hold_on_neutral``）：候选 regime 为中性时沿用上一有效 regime；
3. 确认（``confirm_days``）：候选 regime 与当前有效 regime 不同时，需连续
   ``confirm_days`` 个决策日给出同一候选才切换；
4. 最短持有（``min_hold_days``）：有效 regime 切换后至少持有该决策日数才允许再切换。

默认参数（hysteresis=0、hold_on_neutral=False、confirm_days=1、min_hold_days=0）
下状态层是恒等变换：有效 regime 恒等于候选 regime，存量回测口径不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TimingState:
    """择时状态层在某个决策日结束时的状态。

    Attributes:
        regime: 当日有效 regime（经状态层后实际用于决定仓位）。
        held_days: 有效 regime 已连续持有的决策日数（含当日）。
        pending: 尚未确认的候选 regime；None 表示没有待确认的切换。
        pending_days: 候选 regime 已连续出现的决策日数。
        tier: 当日有效仓位档位序号（``timing.tiers`` 下标）；None 表示未启用档位
            或未命中档位（按 regime 仓位表）。状态层沿用前 regime 时同时沿用该档位。
    """

    regime: str
    held_days: int = 1
    pending: str | None = None
    pending_days: int = 0
    tier: int | None = None

    def to_detail(self) -> dict[str, Any]:
        """转换为择时明细中的状态字段。

        Returns:
            含有效 regime 持有天数与待确认候选的字典。
        """
        return {
            "held_days": self.held_days,
            "pending_regime": self.pending,
            "pending_days": self.pending_days,
        }


def resolve_effective_tier(
    *,
    regime: str,
    raw_regime: str,
    raw_tier: int | None,
    previous: TimingState | None,
    degraded: bool,
    hold_previous_on_degraded: bool,
) -> tuple[int | None, str]:
    """在状态层给出有效 regime 后，确定当日有效仓位档位。

    档位跟随有效 regime：状态层接受当日原始判定（有效 regime 等于原始 regime）时
    采用当日命中的档位；状态层沿用上一 regime（滞回、确认中、无信号维持、最短持有）
    时同时沿用上一决策日的档位，避免"regime 被维持、仓位却跳到另一档"。同一 regime
    内的档位变化不受确认 / 持有约束，当日生效。

    降级日（择时因子全部缺失）的回退是显式策略：只有 ``on_degraded=hold_previous``
    且回退 regime 与上一决策日相同时才沿用上一档位，其余按 regime 仓位表。

    Args:
        regime: 当日有效 regime。
        raw_regime: 当日原始 regime（综合分过阈值或规则判定）。
        raw_tier: 当日原始命中的档位序号；None 表示未命中。
        previous: 上一决策日结束时的状态；None 表示无历史。
        degraded: 当日是否降级。
        hold_previous_on_degraded: 降级策略是否为 hold_previous。

    Returns:
        (有效档位序号, 来源)；来源取 matched（当日命中）/ held（沿用上一档位）/
        none（无档位，按 regime 仓位表）。
    """
    if degraded:
        if hold_previous_on_degraded and previous is not None and previous.regime == regime:
            return (previous.tier, "held") if previous.tier is not None else (None, "none")
        return None, "none"
    if regime == raw_regime:
        return (raw_tier, "matched") if raw_tier is not None else (None, "none")
    if previous is not None and previous.regime == regime and previous.tier is not None:
        return previous.tier, "held"
    return None, "none"


def resolve_candidate_regime(
    raw_regime: str,
    composite: float | None,
    previous_regime: str | None,
    *,
    hysteresis: float,
    hold_on_neutral: bool,
    offensive_threshold: float,
    defensive_threshold: float,
) -> str:
    """在原始 regime 上施加滞回与"无信号维持"，得到当日候选 regime。

    Args:
        raw_regime: 综合分过阈值（或规则组判定）得到的原始 regime。
        composite: 当日综合分；规则模式或降级日为 None，此时滞回不生效。
        previous_regime: 上一决策日的有效 regime；None 表示没有历史。
        hysteresis: 滞回带宽（综合分点数），0 表示不启用。
        hold_on_neutral: 候选为中性时是否沿用上一有效 regime。
        offensive_threshold: 进攻阈值。
        defensive_threshold: 防守阈值。

    Returns:
        当日候选 regime。
    """
    candidate = raw_regime
    if previous_regime is None:
        return candidate
    if hysteresis > 0 and composite is not None:
        # 只延长"留在原 regime"的区间，不阻止直接跨到对侧极端 regime
        if (
            previous_regime == "offensive"
            and candidate == "neutral"
            and composite >= offensive_threshold - hysteresis
        ):
            candidate = "offensive"
        elif (
            previous_regime == "defensive"
            and candidate == "neutral"
            and composite <= defensive_threshold + hysteresis
        ):
            candidate = "defensive"
    if hold_on_neutral and candidate == "neutral":
        candidate = previous_regime
    return candidate


def step_timing_state(
    previous: TimingState | None,
    candidate: str,
    *,
    confirm_days: int,
    min_hold_days: int,
) -> TimingState:
    """按确认天数与最短持有期推进一步状态。

    Args:
        previous: 上一决策日结束时的状态；None 表示首个决策日（直接采用候选）。
        candidate: 当日候选 regime（已施加滞回与无信号维持）。
        confirm_days: 切换所需的连续确认决策日数（≥1，1 表示当日即切换）。
        min_hold_days: 切换后最短持有的决策日数（0 表示不限制）。

    Returns:
        当日结束时的新状态。
    """
    if previous is None:
        return TimingState(regime=candidate)
    if candidate == previous.regime:
        return TimingState(regime=previous.regime, held_days=previous.held_days + 1)
    pending_days = previous.pending_days + 1 if previous.pending == candidate else 1
    if pending_days >= max(1, confirm_days) and previous.held_days >= min_hold_days:
        return TimingState(regime=candidate)
    return TimingState(
        regime=previous.regime,
        held_days=previous.held_days + 1,
        pending=candidate,
        pending_days=pending_days,
    )


def is_identity_state(
    *, hysteresis: float, hold_on_neutral: bool, confirm_days: int, min_hold_days: int
) -> bool:
    """判断状态层参数是否为恒等变换（有效 regime 恒等于原始 regime）。

    Args:
        hysteresis: 滞回带宽。
        hold_on_neutral: 是否无信号维持。
        confirm_days: 确认天数。
        min_hold_days: 最短持有天数。

    Returns:
        恒等时返回 True（实时路径据此跳过历史回放）。
    """
    return hysteresis <= 0 and not hold_on_neutral and confirm_days <= 1 and min_hold_days <= 0
