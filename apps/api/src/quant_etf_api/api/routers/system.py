from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.system import DataQualityResponse, SystemStatusResponse
from quant_etf_api.services.ingest_service import IngestService
from quant_etf_api.services.system_service import SystemService

router = APIRouter(tags=["system"])


@router.get("/system/status", response_model=SystemStatusResponse)
def system_status(db: Session = Depends(get_db)) -> SystemStatusResponse:
    """返回系统运行状态快照，包含数据概览、各表新鲜度和最近运行记录。"""
    return SystemService(db).status()


@router.get("/system/data-quality", response_model=DataQualityResponse)
def data_quality(db: Session = Depends(get_db)) -> DataQualityResponse:
    """返回数据质量总览，包含各 ETF / 指数的新鲜度和覆盖率。

    交易日历不可用时返回 503（由 main.py 的全局异常处理器统一转换）：
    新鲜度必须相对"真实最近交易日"计算，按星期近似会得出错误的缺口结论（C1）。
    """
    result = IngestService(db).check_data_freshness()
    return DataQualityResponse(**result)
