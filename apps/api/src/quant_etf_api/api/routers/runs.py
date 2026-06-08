"""研究运行路由：触发各种后台任务，查询运行状态。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.schemas.pagination import PaginatedResponse
from quant_etf_api.schemas.run import ResearchRunDetail, ResearchRunItemSchema, ResearchRunSummary
from quant_etf_api.services.ingest_service import IngestService
from quant_etf_api.services.run_service import RunService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])

# 后台任务线程池，最大并发 3 个任务
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="bg-task")


def _run_ingest_bg(run_id: str) -> None:
    """在独立 Session 中执行数据摄取，避免与请求 Session 冲突。"""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).run_daily_ingest(run_id)
    except Exception as e:
        logger.exception("数据摄取任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"数据摄取异常: {type(e).__name__}: {e!s}")
    finally:
        db.close()


def _run_strategy_bg(strategy_id: str, run_id: str, params: dict[str, Any] | None) -> None:
    """在独立 Session 中执行策略信号计算。"""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        from quant_etf_api.services.strategy_config_service import StrategyConfigService
        from quant_etf_api.services.strategy_execution_service import StrategyExecutionService

        config_svc = StrategyConfigService(db)
        config = config_svc.get_parsed_config(strategy_id)
        if config is None:
            RunService(db).mark_failed(run_id, f"未找到策略配置: {strategy_id}")
            return
        StrategyExecutionService(db).execute(config, date.today(), run_id, params)
    except Exception as e:
        logger.exception("策略执行任务异常: run_id=%s strategy_id=%s", run_id, strategy_id)
        RunService(db).mark_failed(run_id, f"策略执行异常: {type(e).__name__}: {e!s}")
    finally:
        db.close()


def _run_cold_start_bg(run_id: str) -> None:
    """在独立 Session 中执行冷启动数据拉取。"""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).run_cold_start(run_id)
    except Exception as e:
        logger.exception("冷启动任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"冷启动异常: {type(e).__name__}: {e!s}")
    finally:
        db.close()


def _run_universe_refresh_bg(run_id: str) -> None:
    """在独立 Session 中执行 ETF 池元数据刷新，避免与请求 Session 冲突。"""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        from quant_etf_api.services.universe_service import UniverseService

        UniverseService(db).refresh_all(run_id)
    except Exception as e:
        logger.exception("ETF 池刷新任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"ETF 池刷新异常: {type(e).__name__}: {e!s}")
    finally:
        db.close()


def _run_etf_refresh_bg(run_id: str) -> None:
    """在独立 Session 中刷新 ETF 日线和份额数据。"""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).refresh_etf_data(run_id)
    except Exception as e:
        logger.exception("ETF 数据刷新任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"ETF 数据刷新异常: {type(e).__name__}: {e!s}")
    finally:
        db.close()


def _run_index_refresh_bg(run_id: str) -> None:
    """在独立 Session 中刷新指数日线和估值数据。"""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).refresh_index_data(run_id)
    except Exception as e:
        logger.exception("指数数据刷新任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"指数数据刷新异常: {type(e).__name__}: {e!s}")
    finally:
        db.close()


def _run_macro_refresh_bg(run_id: str) -> None:
    """在独立 Session 中刷新宏观指标数据。"""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).refresh_macro_data(run_id)
    except Exception as e:
        logger.exception("宏观数据刷新任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"宏观数据刷新异常: {type(e).__name__}: {e!s}")
    finally:
        db.close()


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
    """获取运行的子项明细列表（逐 ETF 处理结果）。"""
    return RunService(db).get_run_items(run_id)


# ---------------------------------------------------------------------------
# 触发端点
# ---------------------------------------------------------------------------


@router.post("/runs/universe-refresh")
def refresh_universe(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发 ETF 池元数据刷新，后台线程执行。"""
    summary = RunService(db).create_run("universe_refresh", None, date.today())
    _executor.submit(_run_universe_refresh_bg, summary.run_id)
    return {"status": "accepted", "run_type": "universe_refresh", "run_id": summary.run_id}


@router.post("/runs/daily-ingest")
def daily_ingest(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发全量日频数据摄取（ETF + 指数 + 宏观），后台线程执行。"""
    summary = RunService(db).create_run("daily_ingest", None, date.today())
    _executor.submit(_run_ingest_bg, summary.run_id)
    return {"status": "accepted", "run_type": "daily_ingest", "run_id": summary.run_id}


@router.post("/runs/etf-refresh")
def etf_refresh(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发 ETF 日线和份额数据刷新，后台线程执行。"""
    summary = RunService(db).create_run("etf_refresh", None, date.today())
    _executor.submit(_run_etf_refresh_bg, summary.run_id)
    return {"status": "accepted", "run_type": "etf_refresh", "run_id": summary.run_id}


@router.post("/runs/index-refresh")
def index_refresh(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发指数日线和估值数据刷新，后台线程执行。"""
    summary = RunService(db).create_run("index_refresh", None, date.today())
    _executor.submit(_run_index_refresh_bg, summary.run_id)
    return {"status": "accepted", "run_type": "index_refresh", "run_id": summary.run_id}


@router.post("/runs/macro-refresh")
def macro_refresh(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发宏观指标数据刷新，后台线程执行。"""
    summary = RunService(db).create_run("macro_refresh", None, date.today())
    _executor.submit(_run_macro_refresh_bg, summary.run_id)
    return {"status": "accepted", "run_type": "macro_refresh", "run_id": summary.run_id}


@router.post("/runs/cold-start")
def cold_start(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发冷启动：拉取全部 ETF 和指数从成立至今的全量历史日线数据。"""
    summary = RunService(db).create_run("cold_start", None, date.today())
    _executor.submit(_run_cold_start_bg, summary.run_id)
    return {"status": "accepted", "run_type": "cold_start", "run_id": summary.run_id}


@router.post("/runs/strategies/{strategy_id}/run")
def run_strategy(strategy_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """触发指定策略的信号计算任务，后台线程执行并写入 index_signal 表。"""
    summary = RunService(db).create_run("strategy_run", strategy_id, date.today())
    _executor.submit(_run_strategy_bg, strategy_id, summary.run_id, None)
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

    # 创建新的 run 并提交到线程池
    new_summary = svc.create_run(
        detail.run_type, detail.strategy_id, detail.trade_date or date.today()
    )

    if detail.run_type == "daily_ingest":
        _executor.submit(_run_ingest_bg, new_summary.run_id)
    elif detail.run_type == "strategy_run":
        if detail.strategy_id is None:
            raise HTTPException(status_code=400, detail="策略运行记录缺少 strategy_id")
        _executor.submit(_run_strategy_bg, detail.strategy_id, new_summary.run_id, detail.params)
    elif detail.run_type == "cold_start":
        _executor.submit(_run_cold_start_bg, new_summary.run_id)
    elif detail.run_type == "universe_refresh":
        _executor.submit(_run_universe_refresh_bg, new_summary.run_id)
    elif detail.run_type == "etf_refresh":
        _executor.submit(_run_etf_refresh_bg, new_summary.run_id)
    elif detail.run_type == "index_refresh":
        _executor.submit(_run_index_refresh_bg, new_summary.run_id)
    elif detail.run_type == "macro_refresh":
        _executor.submit(_run_macro_refresh_bg, new_summary.run_id)
    else:
        raise HTTPException(status_code=400, detail=f"不支持重试的运行类型: {detail.run_type}")

    return {"status": "accepted", "run_type": detail.run_type, "run_id": new_summary.run_id}
