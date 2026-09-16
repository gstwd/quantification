from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.system import SystemStatusResponse
from quant_etf_api.services.system_service import SystemService

router = APIRouter(tags=["system"])


@router.get("/system/status", response_model=SystemStatusResponse)
def system_status(db: Session = Depends(get_db)) -> SystemStatusResponse:
    """返回系统运行状态快照，包含数据概览、各表新鲜度和最近运行记录。"""
    return SystemService(db).status()


# 说明：原先的 `GET /system/data-quality`（独立重算新鲜度与字段空值率）已删除。
# 数据质量只保留一个口径——`data_health_snapshot`，由数据管理页
# （`GET /data-management` 与 `GET /data-management/datasets/{dataset_key}`）暴露。
