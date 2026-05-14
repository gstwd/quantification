from datetime import date, datetime
from uuid import uuid4

from quant_etf_api.schemas.run import ResearchRunSummary


class RunService:
    def list_runs(self) -> list[ResearchRunSummary]:
        return [
            ResearchRunSummary(
                run_id=str(uuid4()),
                run_type="daily_ingest",
                trade_date=date.today(),
                status="pending",
                started_at=datetime.utcnow(),
                finished_at=None,
                error_message=None,
            )
        ]
