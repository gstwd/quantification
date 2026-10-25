"""站内告警路由：列表、未读数、标记已读。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.alert import AlertItem, AlertListResponse, AlertReadAllResponse
from quant_etf_api.services.alert_service import AlertService

router = APIRouter(tags=["alerts"])


def _to_item(row: object) -> AlertItem:
    """ORM 行 → 接口模型。"""
    return AlertItem.model_validate(row, from_attributes=True)


@router.get("/alerts", response_model=AlertListResponse)
def list_alerts(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> AlertListResponse:
    """按时间倒序返回告警与未读数（只读）。"""
    svc = AlertService(db)
    return AlertListResponse(
        items=[_to_item(row) for row in svc.list_alerts(unread_only=unread_only, limit=limit)],
        unread_count=svc.unread_count(),
    )


@router.post("/alerts/read-all", response_model=AlertReadAllResponse)
def read_all_alerts(db: Session = Depends(get_db)) -> AlertReadAllResponse:
    """全部标记已读。"""
    return AlertReadAllResponse(updated=AlertService(db).mark_all_read())


@router.post("/alerts/{alert_id}/read", response_model=AlertReadAllResponse)
def read_alert(alert_id: int, db: Session = Depends(get_db)) -> AlertReadAllResponse:
    """单条标记已读。"""
    if not AlertService(db).mark_read(alert_id):
        raise HTTPException(status_code=404, detail=f"告警 {alert_id} 不存在")
    return AlertReadAllResponse(updated=1)
