"""研究运行路由：触发各种后台任务，查询运行状态。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.infra.job_queue.queue import get_job_queue
from quant_etf_api.schemas.pagination import PaginatedResponse
from quant_etf_api.schemas.run import ResearchRunDetail, ResearchRunItemSchema, ResearchRunSummary
from quant_etf_api.services.run_service import RunService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])


def _enqueue_for_run(
    run_type: str,
    run_id: str,
    strategy_id: str | None,
    params: dict[str, Any] | None,
    trade_date: date,
) -> bool:
    """按运行类型将任务入队，返回是否支持该类型。

    Args:
        run_type: 运行类型（与 research_run.run_type 对齐）。
        run_id: 运行记录 ID。
        strategy_id: 关联策略 ID，仅 strategy_run 有值。
        params: 运行参数。
        trade_date: 运行对应交易日，用于 AI 任务去重键。

    Returns:
        True 表示已入队；False 表示不支持的运行类型。
    """
    queue = get_job_queue()
    if run_type == "daily_ingest":
        queue.enqueue("daily_ingest", {"run_id": run_id}, job_key="daily_ingest")
    elif run_type == "strategy_run":
        if not strategy_id:
            return False
        queue.enqueue(
            "strategy_run",
            {"strategy_id": strategy_id, "run_id": run_id, "params": params},
        )
    elif run_type == "cold_start":
        queue.enqueue("cold_start", {"run_id": run_id})
    elif run_type == "index_refresh":
        queue.enqueue("index_refresh", {"run_id": run_id})
    elif run_type == "macro_refresh":
        queue.enqueue("macro_refresh", {"run_id": run_id})
    elif run_type == "startup_fill":
        queue.enqueue("startup_fill", {"run_id": run_id})
    elif run_type == "ai_analysis":
        queue.enqueue("ai_analysis", {"run_id": run_id}, job_key=f"ai_analysis:{trade_date}")
    else:
        return False
    return True


def recover_stuck_runs_on_startup() -> None:
    """应用启动时恢复卡在 pending/running 状态的运行记录。"""
    db = SessionLocal()
    try:
        RunService(db).recover_stuck_runs()
    except Exception:
        logger.exception("启动恢复卡死任务失败")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 查询端点
# ---------------------------------------------------------------------------


@router.get("/runs", response_model=PaginatedResponse[ResearchRunSummary])
def list_runs(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ResearchRunSummary]:
    """分页查询运行记录列表。"""
    items, total = RunService(db).list_runs(offset=offset, limit=limit)
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/runs/{run_id}", response_model=ResearchRunDetail)
def get_run_detail(run_id: str, db: Session = Depends(get_db)) -> ResearchRunDetail:
    """获取单条运行记录的详细信息，包含 metrics 和耗时。"""
    detail = RunService(db).get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"运行记录不存在: {run_id}")
    return detail


@router.get("/runs/{run_id}/items", response_model=list[ResearchRunItemSchema])
def get_run_items(run_id: str, db: Session = Depends(get_db)) -> list[ResearchRunItemSchema]:
    """获取运行的子项明细列表（逐标的处理结果）。"""
    return RunService(db).get_run_items(run_id)


# ---------------------------------------------------------------------------
# 触发端点
# ---------------------------------------------------------------------------


@router.post("/runs/daily-ingest")
def daily_ingest(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发增量日频数据摄取（指数 + 宏观），入队后台任务执行。"""
    summary = RunService(db).create_run("daily_ingest", None, date.today())
    _enqueue_for_run("daily_ingest", summary.run_id, None, None, summary.trade_date or date.today())
    return {"status": "accepted", "run_type": "daily_ingest", "run_id": summary.run_id}


@router.post("/runs/index-refresh")
def index_refresh(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发指数日线和估值数据刷新，入队后台任务执行。"""
    summary = RunService(db).create_run("index_refresh", None, date.today())
    _enqueue_for_run("index_refresh", summary.run_id, None, None, summary.trade_date or date.today())
    return {"status": "accepted", "run_type": "index_refresh", "run_id": summary.run_id}


@router.post("/runs/macro-refresh")
def macro_refresh(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发宏观指标数据刷新，入队后台任务执行。"""
    summary = RunService(db).create_run("macro_refresh", None, date.today())
    _enqueue_for_run("macro_refresh", summary.run_id, None, None, summary.trade_date or date.today())
    return {"status": "accepted", "run_type": "macro_refresh", "run_id": summary.run_id}


@router.post("/runs/cold-start")
def cold_start(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发冷启动：拉取全部指数从成立至今的全量历史日线数据。"""
    summary = RunService(db).create_run("cold_start", None, date.today())
    _enqueue_for_run("cold_start", summary.run_id, None, None, summary.trade_date or date.today())
    return {"status": "accepted", "run_type": "cold_start", "run_id": summary.run_id}


@router.post("/runs/strategies/{strategy_id}/run")
def run_strategy(strategy_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """触发指定策略的信号计算任务，入队后台任务执行并写入 index_signal 表。"""
    summary = RunService(db).create_run("strategy_run", strategy_id, date.today())
    _enqueue_for_run("strategy_run", summary.run_id, strategy_id, None, summary.trade_date or date.today())
    return {"status": "accepted", "strategy_id": strategy_id, "run_id": summary.run_id}


@router.post("/runs/{run_id}/retry")
def retry_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """重试失败的运行记录，创建新的 run 并重新执行相同任务。"""
    svc = RunService(db)
    detail = svc.get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"运行记录不存在: {run_id}")
    if detail.status not in ("failed", "success"):
        raise HTTPException(
            status_code=400, detail=f"只能重试已完成的运行记录，当前状态: {detail.status}"
        )

    # 创建新的 run 并入队对应后台任务
    new_summary = svc.create_run(
        detail.run_type, detail.strategy_id, detail.trade_date or date.today()
    )

    trade_date = new_summary.trade_date or date.today()
    if not _enqueue_for_run(
        detail.run_type, new_summary.run_id, detail.strategy_id, detail.params, trade_date
    ):
        raise HTTPException(status_code=400, detail=f"不支持重试的运行类型: {detail.run_type}")

    return {"status": "accepted", "run_type": detail.run_type, "run_id": new_summary.run_id}
