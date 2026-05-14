from datetime import date, datetime

from pydantic import BaseModel


class ResearchRunSummary(BaseModel):
    run_id: str
    run_type: str
    strategy_id: str | None = None
    trade_date: date | None = None
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
