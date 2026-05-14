from datetime import date, datetime

from pydantic import BaseModel


class EtfSummary(BaseModel):
    etf_code: str
    exchange: str
    name_cn: str
    tracking_index_name: str
    category: str
    is_active: bool


class EtfDetail(EtfSummary):
    fund_full_name: str | None = None
    tracking_index_code: str | None = None
    fund_company: str | None = None
    listing_date: date | None = None
    delisting_date: date | None = None
    is_a_share_etf: bool = True
    data_source: str = "seed"
    updated_at: datetime | None = None
