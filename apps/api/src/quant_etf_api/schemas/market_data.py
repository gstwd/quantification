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
