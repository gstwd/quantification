"""统一数据管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.infra.job_queue.queue import get_job_queue
from quant_etf_api.infra.time import today_cn
from quant_etf_api.schemas.data_management import (
    DataManagementOperationAccepted,
    DataManagementOperationRequest,
    DataManagementOverview,
    DataSetDetailResponse,
)
from quant_etf_api.services.data_management_service import DATASETS, DataManagementService
from quant_etf_api.services.run_service import RunService

router = APIRouter(prefix="/data-management", tags=["data-management"])


@router.get("", response_model=DataManagementOverview)
def get_overview(db: Session = Depends(get_db)) -> DataManagementOverview:
    """返回全部非新闻外部数据集的当前健康总览。"""
    return DataManagementService(db).overview()


@router.get("/datasets/{dataset_key}", response_model=DataSetDetailResponse)
def get_dataset_detail(
    dataset_key: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    partition_key: str | None = Query(default=None, min_length=1, max_length=64),
    db: Session = Depends(get_db),
) -> DataSetDetailResponse:
    """返回一个数据集的质量规则和分页分区快照。"""
    try:
        return DataManagementService(db).detail(dataset_key, offset, limit, partition_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/operations", response_model=DataManagementOperationAccepted)
def create_operation(
    request: DataManagementOperationRequest,
    db: Session = Depends(get_db),
) -> DataManagementOperationAccepted:
    """提交全局、数据集级或单分区级的数据维护任务。

    全局范围只允许同步最新与质量检查；全量重拉必须以确认令牌避免
    浏览器误触导致大范围删除。
    """
    keys = {definition.key for definition in DATASETS}
    if request.dataset_key is not None and request.dataset_key not in keys:
        raise HTTPException(status_code=422, detail=f"未知数据集: {request.dataset_key}")
    if request.dataset_key is None and request.operation not in {"sync_latest", "check"}:
        raise HTTPException(status_code=422, detail="全局范围仅支持同步最新或质量检查")
    if request.partition_key and request.dataset_key is None:
        raise HTTPException(status_code=422, detail="分区操作必须指定数据集")
    if request.operation == "rebuild":
        if request.dataset_key is None:
            raise HTTPException(status_code=422, detail="不支持全局全量重拉")
        scope = request.partition_key or "ALL"
        expected = f"REBUILD:{request.dataset_key}:{scope}"
        if request.confirmation_token != expected:
            raise HTTPException(status_code=422, detail=f"全量重拉确认令牌必须为 {expected}")

    run_type = "data_sync_all" if request.dataset_key is None and request.operation == "sync_latest" else "data_manage_operation"
    params = request.model_dump(exclude_none=True)
    summary = RunService(db).create_run(run_type, None, today_cn(), params=params)
    payload = {"run_id": summary.run_id, **params}
    job_key = (
        "data_sync_all"
        if run_type == "data_sync_all"
        else f"data_manage:{request.operation}:{request.dataset_key or 'all'}:{request.partition_key or 'all'}"
    )
    queue = get_job_queue()
    job_id, created = queue.enqueue_with_status(
        "data_sync_all" if run_type == "data_sync_all" else "data_manage_operation",
        payload,
        job_key=job_key,
    )
    if not created:
        RunService(db).mark_skipped(
            summary.run_id,
            {"reason": "同范围数据管理任务已在执行", "active_job_id": job_id},
        )
        active_payload = queue.find_active_payload(job_key) or {}
        active_run_id = active_payload.get("run_id")
        return DataManagementOperationAccepted(
            status="already_running",
            run_id=str(active_run_id or summary.run_id),
            run_type=run_type,
            operation=request.operation,
        )
    return DataManagementOperationAccepted(
        run_id=summary.run_id,
        run_type=run_type,
        operation=request.operation,
    )
