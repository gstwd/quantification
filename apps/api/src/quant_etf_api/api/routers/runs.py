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


#: 仍可新建 / 重试的运行类型；历史运行记录中的旧类型（daily_ingest / index_refresh /
#: macro_refresh / cold_start / index_rebuild / index_incremental_fill）已收敛到数据管理
#: 操作，不再支持重试。前端 utils/labels.ts 的 RETRYABLE_RUN_TYPES 须与此保持一致。
RETRYABLE_RUN_TYPES: frozenset[str] = frozenset(
    {"strategy_run", "data_sync_all", "data_manage_operation"}
)

#: _enqueue_for_run 的结果：新建任务 / 已有同键活动任务（未新建）/ 不支持的类型
ENQUEUE_CREATED = "created"
ENQUEUE_DUPLICATE = "duplicate"
ENQUEUE_UNSUPPORTED = "unsupported"


def _enqueue_for_run(
    run_type: str,
    run_id: str,
    strategy_id: str | None,
    params: dict[str, Any] | None,
    trade_date: date,
) -> str:
    """按运行类型将任务入队。

    Args:
        run_type: 运行类型（与 research_run.run_type 对齐）。
        run_id: 运行记录 ID。
        strategy_id: 关联策略 ID，仅 strategy_run 有值。
        params: 运行参数。
        trade_date: 运行对应交易日，用于任务去重键。

    Returns:
        ENQUEUE_CREATED：已新建队列任务；ENQUEUE_DUPLICATE：同去重键已有活动任务，
        本次未入队（调用方须把运行记录收口，不能留在 pending）；
        ENQUEUE_UNSUPPORTED：不支持的运行类型或缺少必要参数。
    """
    if run_type not in RETRYABLE_RUN_TYPES:
        return ENQUEUE_UNSUPPORTED
    queue = get_job_queue()
    if run_type == "strategy_run":
        if not strategy_id:
            return ENQUEUE_UNSUPPORTED
        _, created = queue.enqueue_with_status(
            "strategy_run",
            {"strategy_id": strategy_id, "run_id": run_id, "params": params},
        )
    elif run_type == "data_sync_all":
        _, created = queue.enqueue_with_status(
            "data_sync_all", {"run_id": run_id}, job_key="data_sync_all"
        )
    else:
        operation_params = params or {}
        _, created = queue.enqueue_with_status(
            "data_manage_operation",
            {"run_id": run_id, **operation_params},
            job_key=(
                f"data_manage:{operation_params.get('operation', 'check')}:"
                f"{operation_params.get('dataset_key', 'all')}:"
                f"{operation_params.get('partition_key', 'all')}"
            ),
        )
    return ENQUEUE_CREATED if created else ENQUEUE_DUPLICATE


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
# 本路由只保留策略运行与数据管理任务本身的触发入口。


@router.post("/runs/strategies/{strategy_id}/run")
def run_strategy(strategy_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """触发指定策略的信号计算任务，入队后台任务执行并写入 index_signal 表。"""
    svc = RunService(db)
    summary = svc.create_run("strategy_run", strategy_id, date.today())
    outcome = _enqueue_for_run(
        "strategy_run", summary.run_id, strategy_id, None, summary.trade_date or date.today()
    )
    if outcome != ENQUEUE_CREATED:
        svc.mark_skipped(summary.run_id, {"reason": "策略运行任务未入队"})
        return {"status": "skipped", "strategy_id": strategy_id, "run_id": summary.run_id}
    return {"status": "accepted", "strategy_id": strategy_id, "run_id": summary.run_id}


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
    # 先校验类型再建运行记录：不支持的类型不能留下一条永远 pending 的新记录
    if detail.run_type not in RETRYABLE_RUN_TYPES or (
        detail.run_type == "strategy_run" and not detail.strategy_id
    ):
        raise HTTPException(status_code=400, detail=f"不支持重试的运行类型: {detail.run_type}")

    # 创建新的 run 并入队对应后台任务
    new_summary = svc.create_run(
        detail.run_type,
        detail.strategy_id,
        detail.trade_date or today_cn(),
        params=detail.params,
    )

    trade_date = new_summary.trade_date or today_cn()
    outcome = _enqueue_for_run(
        detail.run_type, new_summary.run_id, detail.strategy_id, detail.params, trade_date
    )
    if outcome == ENQUEUE_DUPLICATE:
        # 同去重键已有活动任务：本次没有真正入队，运行记录收口为跳过并说明原因
        svc.mark_skipped(new_summary.run_id, {"reason": "同类任务已在执行，本次重试未入队"})
        return {"status": "skipped", "run_type": detail.run_type, "run_id": new_summary.run_id}
    if outcome == ENQUEUE_UNSUPPORTED:
        svc.mark_skipped(new_summary.run_id, {"reason": f"不支持重试的运行类型: {detail.run_type}"})
        raise HTTPException(status_code=400, detail=f"不支持重试的运行类型: {detail.run_type}")

    return {"status": "accepted", "run_type": detail.run_type, "run_id": new_summary.run_id}
