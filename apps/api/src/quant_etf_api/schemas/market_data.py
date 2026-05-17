from datetime import date, datetime

from pydantic import BaseModel


class DailyBar(BaseModel):
    trade_date: date
    code: str
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    close_price: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    turnover: float | None = None
    source: str = "stub"
    ingested_at: datetime | None = None


class ShareSnapshot(BaseModel):
    trade_date: date
    etf_code: str
    shares_total: float | None = None
    shares_delta: float | None = None
    shares_delta_pct: float | None = None
    nav: float | None = None
    aum: float | None = None
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


class DateRangeResponse(BaseModel):
    """日线数据日期范围响应。"""

    min_date: date | None = None
    max_date: date | None = None


class IndexCreateRequest(BaseModel):
    """添加基准指数请求。"""

    index_code: str
    name_cn: str | None = None


class IndexCreateResponse(BaseModel):
    """添加基准指数响应。"""

    index: BenchmarkIndex
    message: str
