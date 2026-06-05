"""研究运行路由：触发各种后台任务。"""

from __future__ import annotations

import threading
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.schemas.pagination import PaginatedResponse
from quant_etf_api.schemas.run import ResearchRunSummary
from quant_etf_api.services.ingest_service import IngestService
from quant_etf_api.services.run_service import RunService

router = APIRouter(tags=["runs"])


def _run_ingest_bg(run_id: str) -> None:
    """在独立 Session 中执行数据摄取，避免与请求 Session 冲突。"""
    db = SessionLocal()
    try:
        IngestService(db).run_daily_ingest(run_id)
    finally:
        db.close()


def _run_strategy_bg(strategy_id: str, run_id: str, params: dict[str, Any] | None) -> None:
    """在独立 Session 中执行策略信号计算。"""
    db = SessionLocal()
    try:
        from quant_etf_api.services.run_service import RunService
        from quant_etf_api.services.strategy_config_service import StrategyConfigService
        from quant_etf_api.services.strategy_execution_service import StrategyExecutionService

        config_svc = StrategyConfigService(db)
        config = config_svc.get_parsed_config(strategy_id)
        if config is None:
            RunService(db).mark_failed(run_id, f"未找到策略配置: {strategy_id}")
            return
        StrategyExecutionService(db).execute(config, date.today(), run_id, params)
    finally:
        db.close()


def _run_cold_start_bg(run_id: str) -> None:
    """在独立 Session 中执行冷启动数据拉取。"""
    db = SessionLocal()
    try:
        IngestService(db).run_cold_start(run_id)
    finally:
        db.close()


def _run_universe_refresh_bg(run_id: str) -> None:
    """在独立 Session 中执行 ETF 池元数据刷新，避免与请求 Session 冲突。"""
    db = SessionLocal()
    try:
        from quant_etf_api.services.universe_service import UniverseService

        UniverseService(db).refresh_all(run_id)
    finally:
        db.close()


@router.get("/runs", response_model=PaginatedResponse[ResearchRunSummary])
def list_runs(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ResearchRunSummary]:
    items, total = RunService(db).list_runs(offset=offset, limit=limit)
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.post("/runs/universe-refresh")
def refresh_universe(db: Session = Depends(get_db)) -> dict[str, str]:
    summary = RunService(db).create_run("universe_refresh", None, date.today())
    thread = threading.Thread(target=_run_universe_refresh_bg, args=(summary.run_id,), daemon=True)
    thread.start()
    return {"status": "accepted", "run_type": "universe_refresh", "run_id": summary.run_id}


@router.post("/runs/daily-ingest")
def daily_ingest(db: Session = Depends(get_db)) -> dict[str, str]:
    summary = RunService(db).create_run("daily_ingest", None, date.today())
    thread = threading.Thread(target=_run_ingest_bg, args=(summary.run_id,), daemon=True)
    thread.start()
    return {"status": "accepted", "run_type": "daily_ingest", "run_id": summary.run_id}


@router.post("/runs/cold-start")
def cold_start(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发冷启动：拉取全部 ETF 和指数从成立至今的全量历史日线数据。"""
    summary = RunService(db).create_run("cold_start", None, date.today())
    thread = threading.Thread(target=_run_cold_start_bg, args=(summary.run_id,), daemon=True)
    thread.start()
    return {"status": "accepted", "run_type": "cold_start", "run_id": summary.run_id}


@router.post("/runs/strategies/{strategy_id}/run")
def run_strategy(strategy_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """触发指定策略的信号计算任务，后台线程执行并写入 index_signal 表。"""
    summary = RunService(db).create_run("strategy_run", strategy_id, date.today())
    thread = threading.Thread(
        target=_run_strategy_bg,
        args=(strategy_id, summary.run_id, None),
        daemon=True,
    )
    thread.start()
    return {"status": "accepted", "strategy_id": strategy_id, "run_id": summary.run_id}
