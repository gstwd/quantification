from __future__ import annotations
from datetime import date, datetime

from pydantic import BaseModel


class DailyBar(BaseModel):
    trade_date: date
    code: str
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    close_price: float | None = None
    prev_close_price: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    turnover: float | None = None
    source: str = "stub"
    ingested_at: datetime | None = None


class IndexValuation(BaseModel):
    """指数估值数据（PE/PB 及历史分位）。"""

    trade_date: date
    index_code: str
    pe: float | None = None
    pe_percentile: float | None = None
    pb: float | None = None
    pb_percentile: float | None = None
    dividend_yield: float | None = None
    source: str = "akshare"


class MacroIndicatorSchema(BaseModel):
    """宏观经济指标数据。"""

    indicator_code: str
    indicator_name: str
    period: str
    value: float
    unit: str | None = None
    source: str = "akshare"


class BenchmarkIndex(BaseModel):
    """基准指数基本信息。"""

    index_code: str
    index_name: str


class IndexSummary(BaseModel):
    """指数列表汇总数据，包含最新行情和估值快照。

    所有行情/估值字段均可为 None，当对应数据表无记录时返回 null，
    前端通过 — 占位符展示。不触发数据冷启动拉取。
    """

    index_code: str
    index_name: str
    close_price: float | None = None
    change_pct: float | None = None
    bar_date: date | None = None
    pe: float | None = None
    pe_percentile: float | None = None
    pb: float | None = None
    pb_percentile: float | None = None
    dividend_yield: float | None = None
    valuation_date: date | None = None


class DateRangeResponse(BaseModel):
    """日线数据日期范围响应。"""

    min_date: date | None = None
    max_date: date | None = None


class BarQuality(BaseModel):
    """单指数日线数据质量统计。"""

    total: int
    min_date: date | None = None
    max_date: date | None = None
    missing_open: int = 0
    missing_high: int = 0
    missing_low: int = 0
    missing_close: int = 0
    incomplete_rows: int = 0
    incomplete_ratio: float = 0.0


class ValuationQuality(BaseModel):
    """单指数估值数据质量统计。"""

    total: int
    min_date: date | None = None
    max_date: date | None = None
    missing_pe: int = 0
    missing_pb: int = 0
    missing_dividend_yield: int = 0


class IndexDataQuality(BaseModel):
    """指数详情页数据质量总览。"""

    index_code: str
    bars: BarQuality
    valuations: ValuationQuality


class IndexCreateRequest(BaseModel):
    """添加基准指数请求。"""

    index_code: str
    name_cn: str | None = None


class IndexCreateResponse(BaseModel):
    """添加基准指数响应。"""

    index: BenchmarkIndex
    message: str
