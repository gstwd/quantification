"""研究运行路由：触发各种后台任务，查询运行状态。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.infra.time import today_cn

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

    仅支持仍可新建的运行类型；历史运行记录中的旧类型
    （daily_ingest / index_refresh / macro_refresh / cold_start /
    index_rebuild / index_incremental_fill）已收敛到数据管理操作，
    不再支持重试。

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
    if run_type == "strategy_run":
        if not strategy_id:
            return False
        queue.enqueue(
            "strategy_run",
            {"strategy_id": strategy_id, "run_id": run_id, "params": params},
        )
    elif run_type == "ai_analysis":
        queue.enqueue("ai_analysis", {"run_id": run_id}, job_key=f"ai_analysis:{trade_date}")
    elif run_type == "stock_quality_check":
        queue.enqueue(
            "stock_quality_check",
            {"run_id": run_id, "stock_code": (params or {}).get("stock_code", "")},
            job_key=f"stock_quality:{(params or {}).get('stock_code', '')}",
        )
    elif run_type == "stock_data_fill":
        queue.enqueue(
            "stock_data_fill",
            {"run_id": run_id, "stock_code": (params or {}).get("stock_code", "")},
            job_key=f"stock_data_fill:{(params or {}).get('stock_code', '')}",
        )
    elif run_type == "stock_data_rebuild":
        queue.enqueue(
            "stock_data_rebuild",
            {"run_id": run_id, "stock_code": (params or {}).get("stock_code", "")},
            job_key=f"stock_data_rebuild:{(params or {}).get('stock_code', '')}",
        )
    elif run_type == "industry_universe_refresh":
        queue.enqueue("industry_universe_refresh", {"run_id": run_id}, job_key="industry_universe_refresh")
    elif run_type == "industry_bars_refresh":
        queue.enqueue("industry_bars_refresh", {"run_id": run_id}, job_key="industry_bars_refresh")
    elif run_type == "industry_quality_check":
        queue.enqueue(
            "industry_quality_check",
            {"run_id": run_id, "industry_code": (params or {}).get("industry_code", "")},
            job_key=f"industry_quality:{(params or {}).get('industry_code', '')}",
        )
    elif run_type == "industry_data_fill":
        queue.enqueue(
            "industry_data_fill",
            {"run_id": run_id, "industry_code": (params or {}).get("industry_code", "")},
            job_key=f"industry_data_fill:{(params or {}).get('industry_code', '')}",
        )
    elif run_type == "industry_data_rebuild":
        queue.enqueue(
            "industry_data_rebuild",
            {"run_id": run_id, "industry_code": (params or {}).get("industry_code", "")},
            job_key=f"industry_data_rebuild:{(params or {}).get('industry_code', '')}",
        )
    elif run_type == "data_sync_all":
        queue.enqueue("data_sync_all", {"run_id": run_id}, job_key="data_sync_all")
    elif run_type == "data_manage_operation":
        operation_params = params or {}
        queue.enqueue(
            "data_manage_operation",
            {"run_id": run_id, **operation_params},
            job_key=(
                f"data_manage:{operation_params.get('operation', 'check')}:"
                f"{operation_params.get('dataset_key', 'all')}:"
                f"{operation_params.get('partition_key', 'all')}"
            ),
        )
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

# 说明：指数/宏观/行业/个股等全部外部数据的同步、补缺口与全量重拉统一由
# POST /api/data-management/operations 提交（DataManagementService 编排），
# 本路由只保留策略运行、AI 分析与数据管理任务本身的触发入口。


@router.post("/runs/strategies/{strategy_id}/run")
def run_strategy(strategy_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """触发指定策略的信号计算任务，入队后台任务执行并写入 index_signal 表。"""
    summary = RunService(db).create_run("strategy_run", strategy_id, date.today())
    _enqueue_for_run("strategy_run", summary.run_id, strategy_id, None, summary.trade_date or date.today())
    return {"status": "accepted", "strategy_id": strategy_id, "run_id": summary.run_id}


def _enqueue_stock_run(
    run_type: str,
    stock_code: str,
    db: Session,
) -> dict[str, str]:
    """创建单股运行记录并入队对应后台任务。"""
    summary = RunService(db).create_run(
        run_type, None, date.today(), params={"stock_code": stock_code}
    )
    if not _enqueue_for_run(
        run_type,
        summary.run_id,
        None,
        {"stock_code": stock_code},
        summary.trade_date or date.today(),
    ):
        raise HTTPException(status_code=400, detail=f"不支持的单股任务类型: {run_type}")
    return {"status": "accepted", "run_type": run_type, "run_id": summary.run_id}


def _enqueue_industry_run(
    run_type: str,
    industry_code: str,
    db: Session,
) -> dict[str, str]:
    """创建单行业运行记录并入队对应后台任务。"""
    summary = RunService(db).create_run(
        run_type, None, date.today(), params={"industry_code": industry_code}
    )
    if not _enqueue_for_run(
        run_type,
        summary.run_id,
        None,
        {"industry_code": industry_code},
        summary.trade_date or date.today(),
    ):
        raise HTTPException(status_code=400, detail=f"不支持的行业任务类型: {run_type}")
    return {"status": "accepted", "run_type": run_type, "run_id": summary.run_id}


@router.post("/runs/stocks/{stock_code}/quality")
def stock_quality_check(stock_code: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """触发单只股票数据质量检查（重算并落库质量快照）。"""
    return _enqueue_stock_run("stock_quality_check", stock_code, db)


@router.post("/runs/stocks/{stock_code}/fill")
def stock_data_fill(stock_code: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """触发单只股票日线补全（补到最近交易日并刷新质量快照）。"""
    return _enqueue_stock_run("stock_data_fill", stock_code, db)


@router.post("/runs/stocks/{stock_code}/rebuild")
def stock_data_rebuild(stock_code: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """触发单只股票全量重拉（清空旧行后重新拉取入库）。"""
    return _enqueue_stock_run("stock_data_rebuild", stock_code, db)


@router.post("/runs/industry/refresh-info")
def industry_universe_refresh(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发行业目录与成分事件强制刷新（页面顶部“更新行业信息”）。"""
    summary = RunService(db).create_run("industry_universe_refresh", None, date.today())
    _enqueue_for_run(
        "industry_universe_refresh",
        summary.run_id,
        None,
        None,
        summary.trade_date or date.today(),
    )
    return {
        "status": "accepted",
        "run_type": "industry_universe_refresh",
        "run_id": summary.run_id,
    }


@router.post("/runs/industry/refresh-bars")
def industry_bars_refresh(db: Session = Depends(get_db)) -> dict[str, str]:
    """触发全部行业日线增量刷新并重算质量快照。"""
    summary = RunService(db).create_run("industry_bars_refresh", None, date.today())
    _enqueue_for_run(
        "industry_bars_refresh",
        summary.run_id,
        None,
        None,
        summary.trade_date or date.today(),
    )
    return {
        "status": "accepted",
        "run_type": "industry_bars_refresh",
        "run_id": summary.run_id,
    }


@router.post("/runs/industries/{industry_code}/quality")
def industry_quality_check(
    industry_code: str, db: Session = Depends(get_db)
) -> dict[str, str]:
    """触发单个行业数据质量检查（重算并落库质量快照）。"""
    return _enqueue_industry_run("industry_quality_check", industry_code, db)


@router.post("/runs/industries/{industry_code}/fill")
def industry_data_fill(industry_code: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """触发单个行业日线补全（补到最近交易日并刷新质量快照）。"""
    return _enqueue_industry_run("industry_data_fill", industry_code, db)


@router.post("/runs/industries/{industry_code}/rebuild")
def industry_data_rebuild(
    industry_code: str, db: Session = Depends(get_db)
) -> dict[str, str]:
    """触发单个行业全量重拉（清空旧行后重新拉取入库）。"""
    return _enqueue_industry_run("industry_data_rebuild", industry_code, db)


@router.post("/runs/{run_id}/retry")
def retry_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """重试失败的运行记录，创建新的 run 并重新执行相同任务。"""
    svc = RunService(db)
    detail = svc.get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"运行记录不存在: {run_id}")
    if detail.status not in ("failed", "partial_success", "success"):
        raise HTTPException(
            status_code=400, detail=f"只能重试已完成的运行记录，当前状态: {detail.status}"
        )

    # 创建新的 run 并入队对应后台任务
    new_summary = svc.create_run(
        detail.run_type,
        detail.strategy_id,
        detail.trade_date or today_cn(),
        params=detail.params,
    )

    trade_date = new_summary.trade_date or today_cn()
    if not _enqueue_for_run(
        detail.run_type, new_summary.run_id, detail.strategy_id, detail.params, trade_date
    ):
        raise HTTPException(status_code=400, detail=f"不支持重试的运行类型: {detail.run_type}")

    return {"status": "accepted", "run_type": detail.run_type, "run_id": new_summary.run_id}
