"""研究数据瘦身的保护规则（纯函数）。

日常与战役结束后的自动清理只删除回测的逐指数明细（``backtest_index_result``），
回测汇总、逐日结果、稳健性批次、优化会话、研究台账与草稿策略一律保留，
试验次数 N、验收清单与 DSR 校准所需的证据不受影响。

本模块决定一批回测里哪些可以删除逐指数明细、哪些必须保护，不访问数据库。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

# 只有已结束的回测才可瘦身；pending/running 的回测仍可能写入逐指数结果
SLIMMABLE_STATUSES: tuple[str, ...] = ("success", "failed", "cancelled")

# 已结束的后台任务状态；pending/running/paused 的任务永不清理
FINISHED_JOB_STATUSES: tuple[str, ...] = ("success", "failed", "cancelled")

# 尚待人工决策的优化会话状态：会话名下的全部回测（含折回测）都保护
OPEN_SESSION_STATUSES: tuple[str, ...] = ("running", "evaluated")

# 保护原因（按判定顺序）
PROTECT_UNFINISHED = "unfinished"
PROTECT_NON_RESEARCH = "non_research"
PROTECT_LIFECYCLE = "lifecycle"
PROTECT_PENDING_DECISION = "pending_decision"
PROTECT_LATEST_RESEARCH = "latest_research"
PROTECT_REASONS: tuple[str, ...] = (
    PROTECT_UNFINISHED,
    PROTECT_NON_RESEARCH,
    PROTECT_LIFECYCLE,
    PROTECT_PENDING_DECISION,
    PROTECT_LATEST_RESEARCH,
)


@dataclass(frozen=True)
class SlimCandidate:
    """瘦身范围内的一条回测。"""

    backtest_id: str
    strategy_id: str
    status: str
    purpose: str
    optimization_id: str | None
    pruned: bool


@dataclass(frozen=True)
class SessionRef:
    """与瘦身范围相关的优化会话。"""

    optimization_id: str
    status: str
    baseline_backtest_id: str | None
    candidate_backtest_id: str | None


@dataclass(frozen=True)
class SlimPlan:
    """瘦身计划：可删除明细的回测（保持输入顺序）与各类保护计数。"""

    eligible: tuple[str, ...]
    already_pruned: int
    protected: dict[str, int] = field(default_factory=dict)
    total: int = 0


def _protect_reason(
    candidate: SlimCandidate,
    *,
    lifecycle_ids: set[str],
    open_session_ids: set[str],
    pending_backtest_ids: set[str],
    latest_research_ids: set[str],
) -> str | None:
    """返回该回测的保护原因；可以瘦身时返回 None。"""
    if candidate.status not in SLIMMABLE_STATUSES:
        return PROTECT_UNFINISHED
    if candidate.purpose != "research":
        return PROTECT_NON_RESEARCH
    if candidate.backtest_id in lifecycle_ids:
        return PROTECT_LIFECYCLE
    if (
        candidate.optimization_id is not None and candidate.optimization_id in open_session_ids
    ) or candidate.backtest_id in pending_backtest_ids:
        return PROTECT_PENDING_DECISION
    if candidate.backtest_id in latest_research_ids:
        return PROTECT_LATEST_RESEARCH
    return None


def plan_index_detail_slimming(
    candidates: Iterable[SlimCandidate],
    *,
    lifecycle_ids: set[str],
    sessions: Iterable[SessionRef],
    latest_research_ids: set[str],
) -> SlimPlan:
    """按保护规则划分可瘦身回测。

    每条回测按以下顺序归入第一个命中的类别：已瘦身 → 未结束 → 非研究用途
    （验证 / 监控）→ 被生命周期引用 → 待人工决策 → 策略最新一条成功研究回测
    → 可瘦身。

    待人工决策：running / evaluated 会话名下的全部回测，以及 accepted 会话的
    全周期基线 / 候选回测。accepted 会话的折回测不保护——验收清单与 DSR 校准
    只读它们的逐日结果，逐日结果不在瘦身范围内。

    Args:
        candidates: 瘦身范围内的回测（调用方按创建时间升序给出）。
        lifecycle_ids: 被 ``strategy_lifecycle`` 引用的回测 ID。
        sessions: 与范围相关的优化会话。
        latest_research_ids: 每个非变体策略最新一条成功研究回测的 ID
            （稳健性 pool 的 drop_hold1 变体读取它的逐指数明细）。

    Returns:
        瘦身计划。
    """
    session_list = list(sessions)
    open_session_ids = {
        s.optimization_id for s in session_list if s.status in OPEN_SESSION_STATUSES
    }
    pending_backtest_ids: set[str] = set()
    for session in session_list:
        if session.status in OPEN_SESSION_STATUSES or session.status == "accepted":
            for backtest_id in (session.baseline_backtest_id, session.candidate_backtest_id):
                if backtest_id:
                    pending_backtest_ids.add(backtest_id)

    eligible: list[str] = []
    protected: dict[str, int] = {}
    already_pruned = 0
    total = 0
    for candidate in candidates:
        total += 1
        if candidate.pruned:
            already_pruned += 1
            continue
        reason = _protect_reason(
            candidate,
            lifecycle_ids=lifecycle_ids,
            open_session_ids=open_session_ids,
            pending_backtest_ids=pending_backtest_ids,
            latest_research_ids=latest_research_ids,
        )
        if reason is None:
            eligible.append(candidate.backtest_id)
        else:
            protected[reason] = protected.get(reason, 0) + 1
    return SlimPlan(
        eligible=tuple(eligible),
        already_pruned=already_pruned,
        protected=protected,
        total=total,
    )
