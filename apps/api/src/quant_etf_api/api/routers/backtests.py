from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.plugins.registry import StrategyRegistry, build_default_registry
from quant_etf_api.schemas.backtest import (
    BacktestCreateRequest,
    BacktestDetail,
    BacktestDailyResult,
    BacktestEtfResult,
    BacktestSummary,
)
from quant_etf_api.services.backtest_service import BacktestService

router = APIRouter(tags=["backtests"])

# 使用模块级注册表，与 main.py 保持一致
_registry: StrategyRegistry = build_default_registry()


def _run_backtest_bg(backtest_id: str) -> None:
    """在独立 Session 中执行回测，避免与请求 Session 冲突。"""
    db = SessionLocal()
    try:
        BacktestService(db, _registry).run_backtest(backtest_id)
    finally:
        db.close()


@router.post("/backtests", response_model=BacktestSummary, status_code=202)
def create_backtest(req: BacktestCreateRequest, db: Session = Depends(get_db)) -> BacktestSummary:
    """创建回测任务并在后台线程中异步执行，立即返回 pending 状态。"""
    summary = BacktestService(db, _registry).create_backtest(req)
    thread = threading.Thread(target=_run_backtest_bg, args=(summary.backtest_id,), daemon=True)
    thread.start()
    return summary


@router.get("/backtests", response_model=list[BacktestSummary])
def list_backtests(limit: int = 50, db: Session = Depends(get_db)) -> list[BacktestSummary]:
    """返回最近的回测列表。"""
    return BacktestService(db, _registry).list_backtests(limit=limit)


@router.get("/backtests/{backtest_id}", response_model=BacktestDetail)
def get_backtest(backtest_id: str, db: Session = Depends(get_db)) -> BacktestDetail:
    """返回回测详情，含配置信息和汇总指标。"""
    detail = BacktestService(db, _registry).get_backtest(backtest_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="回测记录不存在")
    return detail


@router.get("/backtests/{backtest_id}/daily", response_model=list[BacktestDailyResult])
def get_backtest_daily(backtest_id: str, db: Session = Depends(get_db)) -> list[BacktestDailyResult]:
    """返回回测每日组合绩效，用于权益曲线和回撤图渲染。"""
    return BacktestService(db, _registry).get_daily_results(backtest_id)


@router.get("/backtests/{backtest_id}/etf-results", response_model=list[BacktestEtfResult])
def get_backtest_etf_results(
    backtest_id: str,
    etf_code: str | None = None,
    db: Session = Depends(get_db),
) -> list[BacktestEtfResult]:
    """返回回测每日每 ETF 信号与实际收益，可按 ETF 代码过滤。"""
    return BacktestService(db, _registry).get_etf_results(backtest_id, etf_code=etf_code)
