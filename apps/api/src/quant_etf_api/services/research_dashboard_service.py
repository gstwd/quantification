"""研究看板服务（只读）：研究线、假设台账、研究战役与待人工确认的候选。

供 HTTP GET 与前端研究看板使用，**不写库**：战役进度用只读计算（不会因触顶而
停止战役），优化会话用 :meth:`OptimizationService.inspect`（不补写指标汇总）。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.research import ledger as rules
from quant_etf_api.infra.db.repositories.optimization import OptimizationRepository
from quant_etf_api.infra.db.repositories.research_campaign import ResearchCampaignRepository
from quant_etf_api.infra.db.repositories.strategy_config import StrategyConfigRepository
from quant_etf_api.schemas.research import (
    CampaignDetail,
    CampaignStep,
    CampaignSummary,
    CandidateDetail,
    CandidateSummary,
    HypothesisDetail,
    HypothesisItem,
    LedgerResponse,
    LineageSummary,
    TrialBudget,
)
from quant_etf_api.services.optimization_service import OptimizationService
from quant_etf_api.services.research_campaign_service import ResearchCampaignService
from quant_etf_api.services.research_ledger_service import ResearchLedgerService
from quant_etf_api.services.strategy_config_service import compute_config_hash

logger = logging.getLogger(__name__)

# 待人工确认的优化会话状态：evaluated=待裁决，accepted=已接受、可能尚未 promote
_CANDIDATE_STATUSES = ["evaluated", "accepted"]


class ResearchDashboardService:
    """研究看板的只读聚合服务。"""

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: 数据库会话。
        """
        self._db = db
        self._ledger = ResearchLedgerService(db)
        self._campaigns = ResearchCampaignService(db)
        self._campaign_repo = ResearchCampaignRepository(db)
        self._strategy_repo = StrategyConfigRepository(db)
        self._opt_repo = OptimizationRepository(db)

    # ------------------------------------------------------------------
    # 研究线与台账
    # ------------------------------------------------------------------

    def list_lineages(self) -> list[LineageSummary]:
        """列出全部研究线（根策略为稳健性变体的分组除外），按已用试验次数倒序。

        Returns:
            研究线摘要列表。
        """
        groups: dict[str, list[Any]] = defaultdict(list)
        for row in self._strategy_repo.find_all_rows():
            groups[getattr(row, "lineage_id", None) or row.strategy_id].append(row)
        summaries: list[LineageSummary] = []
        for lineage_id, rows in groups.items():
            root = next((row for row in rows if row.strategy_id == lineage_id), None)
            if root is not None and getattr(root, "is_variant", False):
                continue
            summaries.append(self._lineage_summary(lineage_id, rows, root))
        summaries.sort(key=lambda item: (-item.budget.used, item.lineage_id))
        return summaries

    def ledger(
        self,
        *,
        strategy_id: str | None = None,
        index_code: str | None = None,
        tag: str | None = None,
        status: str | None = None,
    ) -> LedgerResponse:
        """按条件查询假设；给出策略时附研究线摘要（台账按研究线全体成员查询）。

        Args:
            strategy_id: 研究线中任一策略 ID。
            index_code: 指数代码过滤。
            tag: 标签过滤。
            status: 状态过滤（逗号分隔）。

        Returns:
            台账查询结果。

        Raises:
            ValueError: 状态值非法。
        """
        statuses = [item.strip() for item in (status or "").split(",") if item.strip()] or None
        lineage: LineageSummary | None = None
        members: list[str] | None = None
        if strategy_id:
            scope = self._ledger.lineage_scope(strategy_id)
            members = scope["members"]
            rows = [row for sid in members if (row := self._strategy_repo.find_by_id(sid))]
            root = self._strategy_repo.find_by_id(scope["lineage_id"])
            lineage = self._lineage_summary(scope["lineage_id"], rows, root)
        hypotheses = self._ledger.list_hypotheses(
            index_code=index_code, tag=tag, statuses=statuses, limit=1000
        )
        if members is not None:
            member_set = set(members)
            hypotheses = [h for h in hypotheses if h.get("strategy_id") in member_set]
        return LedgerResponse(lineage=lineage, hypotheses=[HypothesisItem(**h) for h in hypotheses])

    def hypothesis(self, hypothesis_id: str) -> HypothesisDetail | None:
        """假设详情。

        Args:
            hypothesis_id: 假设 ID。

        Returns:
            详情，不存在返回 None。
        """
        detail = self._ledger.get_hypothesis(hypothesis_id)
        return HypothesisDetail(**detail) if detail is not None else None

    def _lineage_summary(self, lineage_id: str, rows: list[Any], root: Any) -> LineageSummary:
        """组装研究线摘要。

        N 统计失败时不让整个看板 500：该研究线以 ``error`` 呈现，N 与预算置空值。
        """
        error: str | None = None
        try:
            breakdown = self._ledger.trial_breakdown(lineage_id)
            budget = self._ledger.budget_status(lineage_id)
        except Exception as exc:
            logger.warning("统计研究线 %s 的试验次数失败", lineage_id, exc_info=True)
            self._db.rollback()
            error = f"试验次数统计失败：{exc}"
            breakdown = {}
            budget = {"budget": 0, "used": 0, "remaining": 0}
        all_members = {row.strategy_id for row in rows}
        # 稳健性变体草稿可能有几十个：成员列表只列非变体策略，变体只给数量
        members = [row.strategy_id for row in rows if not getattr(row, "is_variant", False)]
        counts: dict[str, int] = {}
        for item in self._ledger.list_hypotheses(limit=1000):
            if item.get("strategy_id") in all_members:
                counts[item["status"]] = counts.get(item["status"], 0) + 1
        return LineageSummary(
            lineage_id=lineage_id,
            root_display_name=getattr(root, "display_name", "") or "",
            members=members,
            variant_count=len(rows) - len(members),
            n_trials=rules.total_trials(breakdown) if error is None else 0,
            n_trials_breakdown=breakdown,
            budget=TrialBudget(
                budget=budget["budget"], used=budget["used"], remaining=budget["remaining"]
            ),
            hypothesis_counts=counts,
            validation_consumed=any(
                getattr(row, "validation_consumed_at", None) is not None for row in rows
            ),
            running_campaigns=self._campaign_repo.count_running(lineage_id),
            error=error,
        )

    # ------------------------------------------------------------------
    # 研究战役
    # ------------------------------------------------------------------

    def list_campaigns(self, status: str | None = None) -> list[CampaignSummary]:
        """列出研究战役。

        Args:
            status: 状态过滤。

        Returns:
            战役摘要列表。
        """
        return [
            CampaignSummary(**item)
            for item in self._campaigns.list_campaigns(status=status, limit=200)
        ]

    def campaign(self, campaign_id: str) -> CampaignDetail | None:
        """战役详情（只读计算进度，不会触发停止）。

        Args:
            campaign_id: 战役 ID。

        Returns:
            详情，不存在返回 None。
        """
        if self._campaign_repo.find(campaign_id) is None:
            return None
        detail = self._campaigns.status(campaign_id)
        steps = [CampaignStep(**step) for step in detail.pop("steps")]
        return CampaignDetail(**detail, steps=steps)

    # ------------------------------------------------------------------
    # 待人工确认的候选
    # ------------------------------------------------------------------

    def list_candidates(self, lineage_id: str | None = None) -> list[CandidateSummary]:
        """列出待人工确认的优化候选（待裁决，或已接受但基线尚未等于候选配置）。

        Args:
            lineage_id: 研究线过滤。

        Returns:
            候选摘要列表（创建时间倒序）。
        """
        result: list[CandidateSummary] = []
        hashes = self._config_hash_index()
        for session in self._opt_repo.find_by_statuses(_CANDIDATE_STATUSES):
            summary = self._candidate_summary(session, hashes)
            if lineage_id and summary.lineage_id != lineage_id:
                continue
            if summary.status == "accepted" and (summary.promoted or summary.landed_strategy_ids):
                continue
            result.append(summary)
        return result

    def _config_hash_index(self) -> dict[str, list[str]]:
        """非变体策略的配置哈希 → 策略 ID 列表（判定候选是否已落地为某个策略）。"""
        index: dict[str, list[str]] = defaultdict(list)
        for row in self._strategy_repo.find_all_rows():
            if getattr(row, "is_variant", False):
                continue
            index[compute_config_hash(row.config_json or {})].append(row.strategy_id)
        return index

    def candidate(self, optimization_id: str) -> CandidateDetail | None:
        """候选详情：指标汇总与 10 项验收清单（只读）。

        Args:
            optimization_id: 优化会话 ID。

        Returns:
            详情，不存在返回 None。
        """
        session = self._opt_repo.find_by_id(optimization_id)
        if session is None:
            return None
        payload = OptimizationService(self._db).inspect(optimization_id) or {}
        summary = self._candidate_summary(session, self._config_hash_index())
        review = self._campaign_repo.latest_review(optimization_id)
        return CandidateDetail(
            **summary.model_dump(),
            metrics_full=payload.get("metrics_full"),
            fold_summary=payload.get("fold_summary"),
            acceptance_checklist=payload.get("acceptance_checklist") or [],
            missing_backtest_ids=payload.get("missing_backtest_ids") or [],
            review_reason=review.summary if review is not None else None,
        )

    def _candidate_summary(
        self, session: Any, hashes: dict[str, list[str]] | None = None
    ) -> CandidateSummary:
        """组装候选摘要。

        promote 判定：基线当前配置哈希等于候选配置哈希；另建策略判定：除候选草稿与
        基线外，有其他非变体策略的配置哈希等于候选配置哈希。
        """
        baseline = self._strategy_repo.find_by_id(session.strategy_id)
        promoted = (
            baseline is not None
            and compute_config_hash(baseline.config_json or {}) == session.candidate_config_hash
        )
        landed = [
            sid
            for sid in (hashes or {}).get(session.candidate_config_hash, [])
            if sid not in (session.candidate_strategy_id, session.strategy_id)
        ]
        review = self._campaign_repo.latest_review(session.optimization_id)
        lineage_id = (
            (getattr(baseline, "lineage_id", None) or baseline.strategy_id)
            if baseline is not None
            else session.strategy_id
        )
        return CandidateSummary(
            optimization_id=session.optimization_id,
            strategy_id=session.strategy_id,
            candidate_strategy_id=session.candidate_strategy_id,
            lineage_id=lineage_id,
            hypothesis=session.hypothesis,
            status=session.status,
            promoted=promoted,
            landed_strategy_ids=landed,
            review_verdict=review.outcome if review is not None else None,
            campaign_id=review.campaign_id if review is not None else None,
            execution_model=getattr(session, "execution_model", None),
            created_at=session.created_at,
            finished_at=session.finished_at,
        )
