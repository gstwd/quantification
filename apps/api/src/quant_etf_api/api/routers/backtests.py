"""回测路由：创建、查询、执行回测任务。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.api.executor import get_bg_executor
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.schemas.backtest import (
    BacktestComparisonCreateRequest,
    BacktestComparisonDetail,
    BacktestComparisonSummary,
    BacktestCreateRequest,
    BacktestDailyResult,
    BacktestDetail,
    BacktestIndexResult,
    BacktestSummary,
    ComparisonDailyResponse,
)
from quant_etf_api.schemas.pagination import PaginatedResponse
from quant_etf_api.services.backtest_service import BacktestService

router = APIRouter(tags=["backtests"])


def _run_backtest_bg(backtest_id: str) -> None:
    """在独立 Session 中执行回测，避免与请求 Session 冲突。"""
    db = SessionLocal()
    try:
        BacktestService(db).run_backtest(backtest_id)
    finally:
        db.close()


@router.post("/backtests", response_model=BacktestSummary, status_code=202)
def create_backtest(req: BacktestCreateRequest, db: Session = Depends(get_db)) -> BacktestSummary:
    """创建回测任务并在后台线程中异步执行，立即返回 pending 状态。"""
    summary = BacktestService(db).create_backtest(req)
    get_bg_executor().submit(_run_backtest_bg, summary.backtest_id)
    return summary


@router.get("/backtests", response_model=PaginatedResponse[BacktestSummary])
def list_backtests(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PaginatedResponse[BacktestSummary]:
    """分页返回回测列表，按创建时间倒序。"""
    items, total = BacktestService(db).list_backtests(offset=offset, limit=limit)
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


# ── 策略对比回测端点 ───────────────────────────────────────────────
# 注意：对比路由必须定义在 /backtests/{backtest_id} 之前，
# 否则 /backtests/comparisons 会被 {backtest_id} 捕获并返回 404。


def _run_comparison_bg(comparison_id: str) -> None:
    """在独立 Session 中执行对比回测，避免与请求 Session 冲突。"""
    db = SessionLocal()
    try:
        BacktestService(db).run_comparison(comparison_id)
    finally:
        db.close()


@router.post(
    "/backtests/comparisons",
    response_model=BacktestComparisonSummary,
    status_code=202,
)
def create_comparison(
    req: BacktestComparisonCreateRequest,
    db: Session = Depends(get_db),
) -> BacktestComparisonSummary:
    """创建策略对比回测，生成两个子回测并在后台并行执行。"""
    summary = BacktestService(db).create_comparison(req)
    get_bg_executor().submit(_run_comparison_bg, summary.comparison_id)
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
