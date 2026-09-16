from __future__ import annotations
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.market_data import (
    BenchmarkIndex,
    DailyBar,
    DateRangeResponse,
    IndexSummary,
    IndexValuation,
    MacroIndicatorSchema,
)
from quant_etf_api.schemas.pagination import PaginatedResponse
from quant_etf_api.schemas.stock import StockSummary
from quant_etf_api.services.index_service import IndexService
from quant_etf_api.services.ingest_service import IngestService
from quant_etf_api.services.stock_data_service import StockDataService

router = APIRouter(tags=["market-data"])


@router.get("/market-data/indexes", response_model=list[BenchmarkIndex])
def list_benchmark_indexes(
    db: Session = Depends(get_db),
) -> list[BenchmarkIndex]:
    """列出所有活跃的基准指数。"""
    return IndexService(db).list_indexes()


@router.get("/market-data/indexes/summary", response_model=list[IndexSummary])
def list_index_summaries(
    db: Session = Depends(get_db),
) -> list[IndexSummary]:
    """列出所有活跃指数的汇总数据（最新行情 + 估值快照）。

    不触发数据冷启动拉取 —— 若某指数暂无行情或估值数据，对应字段返回 null。
    前端指数列表页只需一次请求即可获取全部所需数据。
    """
    return IngestService(db).get_index_summaries()


@router.get("/market-data/stocks", response_model=PaginatedResponse[StockSummary])
def list_stock_summaries(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=200),
    keyword: str | None = Query(default=None, description="股票代码/名称关键字"),
    industry_code: str | None = Query(default=None, description="申万一级行业代码"),
    status: str | None = Query(default=None, description="active=活跃, delisted=已退市"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[StockSummary]:
    """分页查询个股元数据与日线质量快照（个股数据列表页）。"""
    items, total = StockDataService(db).list_stocks(
        offset=offset,
        limit=limit,
        keyword=keyword,
        industry_code=industry_code,
        status=status,
    )
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


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


# 说明：指数数据质量不再在此暴露独立口径。日线/估值/缺口/异常的统一口径
# 取自 data_health_snapshot，通过 `GET /data-management/datasets/{dataset_key}`
# 按 partition_key=<index_code> 查询（数据管理页同源）。
