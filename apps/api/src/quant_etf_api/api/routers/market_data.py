from fastapi import APIRouter, Query

from quant_etf_api.schemas.market_data import DailyBar, ShareSnapshot
from quant_etf_api.services.ingest_service import IngestService

router = APIRouter(tags=["market-data"])
service = IngestService()


@router.get("/market-data/etfs/{etf_code}/daily-bars", response_model=list[DailyBar])
def etf_daily_bars(etf_code: str, limit: int = Query(default=30, ge=1, le=365)) -> list[DailyBar]:
    return service.get_daily_bars(etf_code, limit)


@router.get("/market-data/etfs/{etf_code}/shares", response_model=list[ShareSnapshot])
def etf_share_history(etf_code: str, limit: int = Query(default=30, ge=1, le=365)) -> list[ShareSnapshot]:
    return service.get_share_history(etf_code, limit)


@router.get("/market-data/indexes/{index_code}/daily-bars", response_model=list[DailyBar])
def index_daily_bars(index_code: str, limit: int = Query(default=30, ge=1, le=365)) -> list[DailyBar]:
    return service.get_daily_bars(index_code, limit)
