"""稳健性验证路由：查询候选集级别的过拟合风险检验结果。

批次创建与统计计算由 CLI（``python -m quant_etf_api.cli robustness ...``）驱动，
因为一次批次会派生出数十个回测任务；HTTP 侧只提供只读查询，
便于前端与 agent 查看已有结论。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.robustness import (
    RobustnessDetail,
    RobustnessListResponse,
)
from quant_etf_api.services.robustness_service import RobustnessService

router = APIRouter(tags=["robustness"])


@router.get("/robustness", response_model=RobustnessListResponse)
def list_robustness_runs(
    limit: int = Query(default=100, ge=1, le=500, description="返回条数上限"),
    db: Session = Depends(get_db),
) -> RobustnessListResponse:
    """返回最近的稳健性验证批次摘要（按创建时间倒序）。"""
    return RobustnessService(db).list_runs(limit=limit)


@router.get("/robustness/{robustness_id}", response_model=RobustnessDetail)
def get_robustness_run(
    robustness_id: str, db: Session = Depends(get_db)
) -> RobustnessDetail:
    """返回稳健性验证批次详情，含变体结果与统计显著性。"""
    detail = RobustnessService(db).get_run(robustness_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="稳健性验证批次不存在")
    return detail
