from __future__ import annotations
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.market_data import (
    BenchmarkIndex,
    DailyBar,
    DateRangeResponse,
    IndexDataQuality,
    IndexSummary,
    IndexValuation,
    MacroIndicatorSchema,
)
from quant_etf_api.services.ingest_service import IngestService

router = APIRouter(tags=["market-data"])


@router.get("/market-data/indexes", response_model=list[BenchmarkIndex])
def list_benchmark_indexes(
    db: Session = Depends(get_db),
) -> list[BenchmarkIndex]:
    """列出所有活跃的基准指数。"""
    return IngestService(db).get_benchmark_indexes()


@router.get("/market-data/indexes/summary", response_model=list[IndexSummary])
def list_index_summaries(
    db: Session = Depends(get_db),
) -> list[IndexSummary]:
    """列出所有活跃指数的汇总数据（最新行情 + 估值快照）。

    不触发数据冷启动拉取 —— 若某指数暂无行情或估值数据，对应字段返回 null。
    前端指数列表页只需一次请求即可获取全部所需数据。
    """
    return IngestService(db).get_index_summaries()


@router.get("/market-data/indexes/{index_code}/daily-bars", response_model=list[DailyBar])
def index_daily_bars(
    index_code: str,
    start_date: date | None = Query(default=None, description="起始日期"),
    end_date: date | None = Query(default=None, description="结束日期"),
    limit: int = Query(default=30, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[DailyBar]:
    """指数日线行情（读穿透缓存，冷启动时从 AkShare 拉取）。"""
    return IngestService(db).get_index_daily_bars(
        index_code, limit, start_date=start_date, end_date=end_date
    )


@router.get("/market-data/indexes/{index_code}/valuation", response_model=list[IndexValuation])
def index_valuation(
    index_code: str,
    start_date: date | None = Query(default=None, description="起始日期"),
    end_date: date | None = Query(default=None, description="结束日期"),
    limit: int = Query(default=30, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[IndexValuation]:
    """指数 PE/PB 估值历史（读穿透缓存，冷启动时从 AkShare 拉取）。"""
    return IngestService(db).get_index_valuation(
        index_code, limit, start_date=start_date, end_date=end_date
    )


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


# --- 日期范围元数据端点 ---


@router.get("/market-data/indexes/{index_code}/date-range", response_model=DateRangeResponse)
def index_date_range(
    index_code: str,
    db: Session = Depends(get_db),
) -> DateRangeResponse:
    """返回指定指数日线数据的日期范围。"""
    min_d, max_d = IngestService(db).get_index_date_range(index_code)
    return DateRangeResponse(min_date=min_d, max_date=max_d)


@router.get("/market-data/indexes/{index_code}/data-quality", response_model=IndexDataQuality)
def index_data_quality(
    index_code: str,
    db: Session = Depends(get_db),
) -> IndexDataQuality:
    """返回指定指数的数据质量统计：日线覆盖、OHLC 缺失、估值覆盖与缺失。"""
    return IngestService(db).get_index_data_quality(index_code)
