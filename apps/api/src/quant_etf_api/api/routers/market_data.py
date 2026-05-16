from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.market_data import (
    BenchmarkIndex,
    DailyBar,
    IndexValuation,
    MacroIndicatorSchema,
    ShareSnapshot,
)
from quant_etf_api.services.ingest_service import IngestService

router = APIRouter(tags=["market-data"])


@router.get("/market-data/indexes", response_model=list[BenchmarkIndex])
def list_benchmark_indexes(
    db: Session = Depends(get_db),
) -> list[BenchmarkIndex]:
    """列出所有基准指数。"""
    return IngestService(db).get_benchmark_indexes()


@router.get("/market-data/etfs/{etf_code}/daily-bars", response_model=list[DailyBar])
def etf_daily_bars(
    etf_code: str,
    limit: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[DailyBar]:
    """ETF 日线行情（读穿透缓存，冷启动时从腾讯 API 拉取）。"""
    return IngestService(db).get_daily_bars(etf_code, limit)


@router.get("/market-data/etfs/{etf_code}/shares", response_model=list[ShareSnapshot])
def etf_share_history(
    etf_code: str,
    limit: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[ShareSnapshot]:
    """ETF 份额历史（读穿透缓存，冷启动时从东方财富 API 拉取）。"""
    return IngestService(db).get_share_history(etf_code, limit)


@router.get("/market-data/indexes/{index_code}/daily-bars", response_model=list[DailyBar])
def index_daily_bars(
    index_code: str,
    limit: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[DailyBar]:
    """指数日线行情（读穿透缓存，冷启动时从 AkShare 拉取）。"""
    return IngestService(db).get_index_daily_bars(index_code, limit)


@router.get("/market-data/indexes/{index_code}/valuation", response_model=list[IndexValuation])
def index_valuation(
    index_code: str,
    limit: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[IndexValuation]:
    """指数 PE/PB 估值历史（读穿透缓存，冷启动时从 AkShare 拉取）。"""
    return IngestService(db).get_index_valuation(index_code, limit)


@router.get("/market-data/macro/{indicator_code}", response_model=list[MacroIndicatorSchema])
def macro_indicators(
    indicator_code: str,
    limit: int = Query(default=60, ge=1, le=600),
    db: Session = Depends(get_db),
) -> list[MacroIndicatorSchema]:
    """宏观指标数据（读穿透缓存，冷启动时从 AkShare 拉取）。

    indicator_code 可选值：cpi, pmi, lpr1y, lpr5y
    """
    return IngestService(db).get_macro_indicators(indicator_code, limit)
