"""个股数据页面相关响应模型。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class StockSummary(BaseModel):
    """个股列表摘要（元数据 + 日线质量快照）。"""

    stock_code: str
    name_cn: str
    industry_code: str | None = None
    industry_name: str | None = None
    ipo_date: date | None = None
    delist_date: date | None = None
    is_active: bool = True
    data_start_date: date | None = None
    data_end_date: date | None = None
    bar_count: int | None = None
    missing_day_count: int | None = None
    quality_checked_at: datetime | None = None
