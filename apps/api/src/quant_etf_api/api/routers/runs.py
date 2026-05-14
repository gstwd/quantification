from fastapi import APIRouter

from quant_etf_api.schemas.run import ResearchRunSummary
from quant_etf_api.services.run_service import RunService

router = APIRouter(tags=["runs"])
service = RunService()


@router.get("/runs", response_model=list[ResearchRunSummary])
def list_runs() -> list[ResearchRunSummary]:
    return service.list_runs()


@router.post("/runs/universe-refresh")
def refresh_universe() -> dict[str, str]:
    return {"status": "accepted", "run_type": "universe_refresh"}


@router.post("/runs/daily-ingest")
def daily_ingest() -> dict[str, str]:
    return {"status": "accepted", "run_type": "daily_ingest"}


@router.post("/runs/strategies/{strategy_id}/run")
def run_strategy(strategy_id: str) -> dict[str, str]:
    return {"status": "accepted", "strategy_id": strategy_id}
