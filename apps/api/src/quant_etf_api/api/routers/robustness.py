"""稳健性验证路由：查询候选集级别的过拟合风险检验结果，并提供批次控制。

批次创建与统计计算由 CLI（``python -m quant_etf_api.cli robustness ...``）驱动，
因为一次批次会派生出数十个回测任务；HTTP 侧提供只读查询，以及批次级
取消/暂停/恢复（B2），便于在批次积压时及时止损。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.robustness import (
    RobustnessControlResponse,
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


@router.post(
    "/robustness/{robustness_id}/cancel",
    response_model=RobustnessControlResponse,
    status_code=202,
)
def cancel_robustness_run(
    robustness_id: str, db: Session = Depends(get_db)
) -> RobustnessControlResponse:
    """取消整批稳健性回测（B2）。

    未开始的任务直接取消；运行中的任务打协作取消标记，由回测主循环在
    安全检查点退出。批次状态落为 cancelled。
    """
    try:
        result = RobustnessService(db).cancel(robustness_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return RobustnessControlResponse(**result)


@router.post(
    "/robustness/{robustness_id}/pause",
    response_model=RobustnessControlResponse,
    status_code=202,
)
def pause_robustness_run(
    robustness_id: str, db: Session = Depends(get_db)
) -> RobustnessControlResponse:
    """暂停批次中尚未开始的任务（B2），运行中的任务自然结束。"""
    try:
        result = RobustnessService(db).pause(robustness_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return RobustnessControlResponse(**result)


@router.post(
    "/robustness/{robustness_id}/resume",
    response_model=RobustnessControlResponse,
    status_code=202,
)
def resume_robustness_run(
    robustness_id: str, db: Session = Depends(get_db)
) -> RobustnessControlResponse:
    """恢复批次中被暂停的任务（B2）。"""
    try:
        result = RobustnessService(db).resume(robustness_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return RobustnessControlResponse(**result)
