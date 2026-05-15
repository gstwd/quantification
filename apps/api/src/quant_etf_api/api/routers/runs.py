from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.run import ResearchRunSummary
from quant_etf_api.services.run_service import RunService

router = APIRouter(tags=["runs"])


@router.get("/runs", response_model=list[ResearchRunSummary])
def list_runs(db: Session = Depends(get_db)) -> list[ResearchRunSummary]:
    return RunService(db).list_runs()


@router.post("/runs/universe-refresh")
def refresh_universe(db: Session = Depends(get_db)) -> dict[str, str]:
    # 触发 ETF 标的列表刷新任务，当前为异步占位，实际执行逻辑待实现
    RunService(db).create_run("universe_refresh", None, date.today())
    return {"status": "accepted", "run_type": "universe_refresh"}


@router.post("/runs/daily-ingest")
def daily_ingest(db: Session = Depends(get_db)) -> dict[str, str]:
    # 触发全量 ETF 日频数据入库任务
    RunService(db).create_run("daily_ingest", None, date.today())
    return {"status": "accepted", "run_type": "daily_ingest"}


@router.post("/runs/strategies/{strategy_id}/run")
def run_strategy(strategy_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    # 触发指定策略的信号计算任务
    RunService(db).create_run("strategy_run", strategy_id, date.today())
    return {"status": "accepted", "strategy_id": strategy_id}
