"""研究看板路由（只读）：研究线、假设台账、研究战役与待人工确认的候选。

写操作（登记假设、开启 / 驱动战役、finish / promote）只在 CLI 中执行；
HTTP 侧只提供查询，供人工在浏览器里审阅自动研究循环的进度与证据。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.research import (
    CampaignDetail,
    CampaignSummary,
    CandidateDetail,
    CandidateSummary,
    HypothesisDetail,
    LedgerResponse,
    LineageSummary,
)
from quant_etf_api.services.research_dashboard_service import ResearchDashboardService

router = APIRouter(tags=["research"])


@router.get("/research/lineages", response_model=list[LineageSummary])
def list_lineages(db: Session = Depends(get_db)) -> list[LineageSummary]:
    """列出研究线：成员、试验次数 N、预算与假设状态计数。"""
    return ResearchDashboardService(db).list_lineages()


@router.get("/research/hypotheses", response_model=LedgerResponse)
def list_hypotheses(
    strategy_id: str | None = Query(default=None, description="研究线中任一策略 ID"),
    index_code: str | None = Query(default=None, description="指数代码"),
    tag: str | None = Query(default=None, description="标签"),
    status: str | None = Query(default=None, description="状态，逗号分隔"),
    db: Session = Depends(get_db),
) -> LedgerResponse:
    """查询研究假设台账；给出策略时按其研究线过滤并附研究线摘要。"""
    try:
        return ResearchDashboardService(db).ledger(
            strategy_id=strategy_id, index_code=index_code, tag=tag, status=status
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/research/hypotheses/{hypothesis_id}", response_model=HypothesisDetail)
def get_hypothesis(hypothesis_id: str, db: Session = Depends(get_db)) -> HypothesisDetail:
    """假设详情：事先登记的判定标准、挂靠的全部试验与子假设。"""
    detail = ResearchDashboardService(db).hypothesis(hypothesis_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="研究假设不存在")
    return detail


@router.get("/research/campaigns", response_model=list[CampaignSummary])
def list_campaigns(
    status: str | None = Query(default=None, description="running / stopped / completed"),
    db: Session = Depends(get_db),
) -> list[CampaignSummary]:
    """列出研究战役（创建时间倒序）。"""
    return ResearchDashboardService(db).list_campaigns(status=status)


@router.get("/research/campaigns/{campaign_id}", response_model=CampaignDetail)
def get_campaign(campaign_id: str, db: Session = Depends(get_db)) -> CampaignDetail:
    """战役详情：限额使用、步骤、花费与战役内假设（只读，不会触发停止）。"""
    detail = ResearchDashboardService(db).campaign(campaign_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="研究战役不存在")
    return detail


@router.get("/research/candidates", response_model=list[CandidateSummary])
def list_candidates(
    lineage_id: str | None = Query(default=None, description="研究线 ID"),
    db: Session = Depends(get_db),
) -> list[CandidateSummary]:
    """待人工确认的优化候选：待裁决，或已接受但尚未 promote。"""
    return ResearchDashboardService(db).list_candidates(lineage_id=lineage_id)


@router.get("/research/candidates/{optimization_id}", response_model=CandidateDetail)
def get_candidate(optimization_id: str, db: Session = Depends(get_db)) -> CandidateDetail:
    """候选详情：全区间指标、逐折汇总与 10 项验收清单（含逐项证据）。"""
    detail = ResearchDashboardService(db).candidate(optimization_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="优化会话不存在")
    return detail
