from datetime import date

from pydantic import BaseModel

from quant_etf_api.schemas.types import UtcDatetime


class ResearchRunSummary(BaseModel):
    run_id: str
    run_type: str
    strategy_id: str | None = None
    trade_date: date | None = None
    status: str
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error_message: str | None = None
