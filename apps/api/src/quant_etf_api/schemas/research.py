"""研究看板 Schema：研究线、假设台账、研究战役与待确认候选（只读）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from quant_etf_api.schemas.types import UtcDatetime


class TrialBudget(BaseModel):
    """研究线试验预算。

    Attributes:
        budget: 预算上限。
        used: 已用试验次数（N 的原始和）。
        remaining: 剩余次数（可为负，表示已超预算放行）。
    """

    budget: int
    used: int
    remaining: int


class LineageSummary(BaseModel):
    """研究线摘要。

    Attributes:
        lineage_id: 研究线 ID（根策略 ID）。
        root_display_name: 根策略显示名。
        members: 成员策略 ID（不含稳健性变体草稿）。
        variant_count: 稳健性变体草稿数量。
        n_trials: 多重检验的试验次数 N（至少为 1）。
        n_trials_breakdown: N 的来源拆分。
        budget: 试验预算使用情况。
        hypothesis_counts: 研究线内假设按状态计数。
        validation_consumed: 研究线是否已消费验证期。
        running_campaigns: 运行中的研究战役数。
        error: N 统计失败时的错误信息（此时 n_trials 与预算为空值，不可据此判断）。
    """

    lineage_id: str
    root_display_name: str = ""
    members: list[str] = Field(default_factory=list)
    variant_count: int = 0
    n_trials: int
    n_trials_breakdown: dict[str, int] = Field(default_factory=dict)
    budget: TrialBudget
    hypothesis_counts: dict[str, int] = Field(default_factory=dict)
    validation_consumed: bool = False
    running_campaigns: int = 0
    error: str | None = None


class HypothesisItem(BaseModel):
    """研究假设。"""

    hypothesis_id: str
    strategy_id: str | None = None
    index_codes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    category: str
    statement: str
    rationale: str | None = None
    pre_registration: dict[str, Any] | None = None
    status: str
    conclusion: str | None = None
    parent_id: str | None = None
    campaign_id: str | None = None
    created_by: str | None = None
    created_at: UtcDatetime | None = None
    closed_at: UtcDatetime | None = None
    trial_rows: int = 0


class TrialItem(BaseModel):
    """研究试验。"""

    trial_id: str
    hypothesis_id: str | None = None
    strategy_id: str
    kind: str
    ref_id: str | None = None
    execution_model: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    trial_count: int
    counted_in_n: bool
    is_estimated: bool = False
    note: str | None = None
    summary: dict[str, Any] | None = None
    created_at: UtcDatetime | None = None


class HypothesisDetail(HypothesisItem):
    """假设详情（含挂靠试验与子假设）。"""

    trials: list[TrialItem] = Field(default_factory=list)
    children: list[dict[str, Any]] = Field(default_factory=list)


class LedgerResponse(BaseModel):
    """台账查询结果。

    Attributes:
        lineage: 按策略查询时给出所在研究线摘要。
        hypotheses: 假设列表（登记时间倒序）。
    """

    lineage: LineageSummary | None = None
    hypotheses: list[HypothesisItem] = Field(default_factory=list)


class CampaignSummary(BaseModel):
    """研究战役摘要。"""

    campaign_id: str
    objective: str
    strategy_id: str
    lineage_id: str
    index_codes: list[str] = Field(default_factory=list)
    status: str
    max_trials: int
    max_hours: float
    max_rounds: int
    max_consecutive_rejects: int
    max_diagnostics: int = 30
    diagnostics_used: int = 0
    max_step_usd: float | None = None
    model: str | None = None
    trials_at_start: int
    stop_reason: str | None = None
    created_by: str | None = None
    created_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None


class CampaignStep(BaseModel):
    """研究战役步骤。"""

    step_id: str
    round: int
    role: str
    status: str
    outcome: str | None = None
    hypothesis_id: str | None = None
    optimization_id: str | None = None
    summary: str | None = None
    error: str | None = None
    cost_usd: float | None = None
    agent_session_id: str | None = None
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None


class CampaignDetail(CampaignSummary):
    """研究战役详情。

    Attributes:
        progress: 限额使用与下一步安排（只读计算，不会触发停止）。
        steps: 全部步骤。
        hypotheses: 战役内假设摘要。
        total_cost_usd: agent 总花费。
    """

    progress: dict[str, Any] = Field(default_factory=dict)
    steps: list[CampaignStep] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    total_cost_usd: float = 0.0


class CandidateSummary(BaseModel):
    """待人工确认的优化候选。

    Attributes:
        optimization_id: 优化会话 ID。
        strategy_id: 基线策略。
        candidate_strategy_id: 候选策略。
        lineage_id: 所在研究线。
        hypothesis: 会话假设。
        status: evaluated=待裁决；accepted=已接受、待 promote 或另建策略。
        promoted: 基线当前配置是否已等于候选配置。
        landed_strategy_ids: 已采用候选配置的其他策略（另建策略落地）。
        review_verdict: 研究战役评审员结论（无评审时为 None）。
        campaign_id: 评审所在战役。
        execution_model: 执行模型。
        created_at: 会话创建时间。
        finished_at: 会话结束时间。
    """

    optimization_id: str
    strategy_id: str
    candidate_strategy_id: str
    lineage_id: str
    hypothesis: str
    status: str
    promoted: bool = False
    landed_strategy_ids: list[str] = Field(default_factory=list)
    review_verdict: str | None = None
    campaign_id: str | None = None
    execution_model: str | None = None
    created_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None


class CandidateDetail(CandidateSummary):
    """候选详情：指标汇总与 10 项验收清单（逐项证据）。"""

    metrics_full: dict[str, Any] | None = None
    fold_summary: dict[str, Any] | None = None
    acceptance_checklist: list[dict[str, Any]] = Field(default_factory=list)
    missing_backtest_ids: list[str] = Field(default_factory=list)
    review_reason: str | None = None
