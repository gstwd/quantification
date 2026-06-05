from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.pagination import PaginatedResponse
from quant_etf_api.schemas.signal import SignalRow
from quant_etf_api.services.signal_service import SignalService

router = APIRouter(tags=["signals"])


@router.get("/signals/latest", response_model=PaginatedResponse[SignalRow])
def latest_signals(
    strategy_id: str = Query(...),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PaginatedResponse[SignalRow]:
    """分页返回指定策略最新交易日的所有 ETF 信号，按得分降序。"""
    items, total = SignalService(db).latest_signals(strategy_id, offset=offset, limit=limit)
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/signals/history", response_model=PaginatedResponse[SignalRow])
def signal_history(
    strategy_id: str = Query(...),
    etf_code: str = Query(...),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PaginatedResponse[SignalRow]:
    """分页查询某策略在某 ETF 上的历史信号。"""
    items, total = SignalService(db).signal_history(
        strategy_id, etf_code, offset=offset, limit=limit
    )
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)
