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
