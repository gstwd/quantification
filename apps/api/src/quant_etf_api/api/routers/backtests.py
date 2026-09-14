"""回测路由：创建、查询、执行回测任务。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.infra.job_queue.queue import backtest_job_key, get_job_queue
from quant_etf_api.schemas.backtest import (
    BacktestCancelResponse,
    BacktestComparisonCreateRequest,
    BacktestComparisonDetail,
    BacktestComparisonSummary,
    BacktestCreateRequest,
    BacktestDailyResult,
    BacktestDetail,
    BacktestIndexResult,
    BacktestSummary,
    ComparisonDailyResponse,
    ValidationUsageResponse,
)
from quant_etf_api.schemas.pagination import PaginatedResponse
from quant_etf_api.services.backtest_service import BacktestService

router = APIRouter(tags=["backtests"])


@router.post("/backtests", response_model=BacktestSummary, status_code=202)
def create_backtest(req: BacktestCreateRequest, db: Session = Depends(get_db)) -> BacktestSummary:
    """创建回测任务并入队异步执行，立即返回 pending 状态。

    研究类（purpose=research）回测越过研究期末端会被拒绝（422），
    验证与监控类回测允许使用验证期数据但会留痕。
    """
    try:
        summary = BacktestService(db).create_backtest(req)
    except ValueError as e:
        # 策略配置校验失败（未配置 portfolio / 引用未知因子等）或
        # 回测区间越过研究期边界 → 422
        raise HTTPException(status_code=422, detail=str(e))
    get_job_queue().enqueue(
        "backtest", {"backtest_id": summary.backtest_id},
        job_key=backtest_job_key(summary.backtest_id),
    )
    return summary


# 注意：该路由必须定义在 /backtests/{backtest_id} 之前，
# 否则 validation-usage 会被 {backtest_id} 捕获并返回 404。
@router.get("/backtests/validation-usage", response_model=ValidationUsageResponse)
def get_validation_usage(
    limit: int = Query(default=200, ge=1, le=1000, description="返回条数上限"),
    db: Session = Depends(get_db),
) -> ValidationUsageResponse:
    """返回所有使用验证期数据的回测记录，用于样本外留痕审计。

    验证期数据只能用于否决、不能用于确认；只要被观察过就应留痕，
    以避免"看过结果再改策略"造成的隐性过拟合。
    """
    return BacktestService(db).list_validation_usage(limit=limit)


@router.get("/backtests", response_model=PaginatedResponse[BacktestSummary])
def list_backtests(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    strategy_id: str | None = Query(default=None, description="策略 ID，精确匹配"),
    status: str | None = Query(
        default=None,
        description="回测状态过滤：pending/running/success/failed/cancelled",
    ),
    purpose: Literal["research", "validation", "monitor"] | None = Query(
        default=None, description="回测用途过滤"
    ),
    created_from: date | None = Query(default=None, description="创建日期起点（含）"),
    created_to: date | None = Query(default=None, description="创建日期终点（含）"),
    order_by: Literal["created_at", "started_at", "finished_at"] = Query(
        default="created_at", description="排序字段"
    ),
    descending: bool = Query(default=True, description="是否倒序"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[BacktestSummary]:
    """分页返回回测列表，支持状态/用途/策略/时间范围筛选与排序（B4）。"""
    if created_from and created_to and created_from > created_to:
        raise HTTPException(status_code=422, detail="创建日期起点不能晚于终点")
    items, total = BacktestService(db).list_backtests(
        offset=offset,
        limit=limit,
        strategy_id=strategy_id,
        created_from=created_from,
        created_to=created_to,
        status=status,
        purpose=purpose,
        order_by=order_by,
        descending=descending,
    )
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


# ── 策略对比回测端点 ───────────────────────────────────────────────
# 注意：对比路由必须定义在 /backtests/{backtest_id} 之前，
# 否则 /backtests/comparisons 会被 {backtest_id} 捕获并返回 404。


@router.post(
    "/backtests/comparisons",
    response_model=BacktestComparisonSummary,
    status_code=202,
)
def create_comparison(
    req: BacktestComparisonCreateRequest,
    db: Session = Depends(get_db),
) -> BacktestComparisonSummary:
    """创建策略对比回测，入队对比任务（由队列派发两个子回测）。"""
    try:
        summary = BacktestService(db).create_comparison(req)
    except ValueError as e:
        # 任一策略配置校验失败 → 422，不创建对比记录
        raise HTTPException(status_code=422, detail=str(e))
    get_job_queue().enqueue("comparison", {"comparison_id": summary.comparison_id})
    return summary


@router.get(
    "/backtests/comparisons",
    response_model=PaginatedResponse[BacktestComparisonSummary],
)
def list_comparisons(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PaginatedResponse[BacktestComparisonSummary]:
    """分页返回对比回测列表，按创建时间倒序。"""
    items, total = BacktestService(db).list_comparisons(offset=offset, limit=limit)
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.get(
    "/backtests/comparisons/{comparison_id}",
    response_model=BacktestComparisonDetail,
)
def get_comparison(
    comparison_id: str,
    db: Session = Depends(get_db),
) -> BacktestComparisonDetail:
    """返回对比回测详情，含两个子回测的完整信息和对比指标。"""
    detail = BacktestService(db).get_comparison(comparison_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="对比记录不存在")
    return detail


@router.get(
    "/backtests/comparisons/{comparison_id}/daily",
    response_model=ComparisonDailyResponse,
)
def get_comparison_daily(
    comparison_id: str,
    db: Session = Depends(get_db),
) -> ComparisonDailyResponse:
    """返回两个策略的每日组合绩效，用于叠加图表渲染。"""
    return BacktestService(db).get_comparison_daily(comparison_id)


@router.get("/backtests/{backtest_id}", response_model=BacktestDetail)
def get_backtest(backtest_id: str, db: Session = Depends(get_db)) -> BacktestDetail:
    """返回回测详情，含配置信息和汇总指标。"""
    detail = BacktestService(db).get_backtest(backtest_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="回测记录不存在")
    return detail


@router.post(
    "/backtests/{backtest_id}/cancel",
    response_model=BacktestCancelResponse,
    status_code=202,
)
def cancel_backtest(backtest_id: str, db: Session = Depends(get_db)) -> BacktestCancelResponse:
    """请求取消回测（B2）。

    未开始的回测直接落为 cancelled；运行中的回测只打协作取消标记，
    由回测主循环在安全检查点退出（已提交的日结果保留）。
    """
    try:
        result = BacktestService(db).cancel_backtest(backtest_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return BacktestCancelResponse(**result)


@router.get("/backtests/{backtest_id}/daily", response_model=list[BacktestDailyResult])
def get_backtest_daily(
    backtest_id: str, db: Session = Depends(get_db)
) -> list[BacktestDailyResult]:
    """返回回测每日组合绩效，用于权益曲线和回撤图渲染。"""
    return BacktestService(db).get_daily_results(backtest_id)


@router.get("/backtests/{backtest_id}/index-results", response_model=list[BacktestIndexResult])
def get_backtest_index_results(
    backtest_id: str,
    index_code: str | None = None,
    db: Session = Depends(get_db),
) -> list[BacktestIndexResult]:
    """返回回测每日每指数信号与实际收益，可按指数代码过滤。"""
    return BacktestService(db).get_index_results(backtest_id, index_code=index_code)
