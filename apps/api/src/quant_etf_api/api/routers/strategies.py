from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.strategy import AllocationResponse, StrategyDetail
from quant_etf_api.services.strategy_service import StrategyService

router = APIRouter(tags=["strategies"])
# 模块级单例：策略注册表在进程生命周期内不变，无需每次请求重建
service = StrategyService()


@router.get("/strategies", response_model=list[StrategyDetail])
def list_strategies() -> list[StrategyDetail]:
    """返回所有已注册策略的摘要列表。"""
    return service.list_strategies()


@router.get("/strategies/{strategy_id}", response_model=StrategyDetail)
def get_strategy(strategy_id: str) -> StrategyDetail:
    """按 ID 获取策略详情。"""
    strategy = service.get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


@router.get("/strategies/{strategy_id}/allocation", response_model=AllocationResponse)
def run_allocation(
    strategy_id: str,
    db: Session = Depends(get_db),
) -> AllocationResponse:
    """运行资产配置决策管线，返回择时、排名、仓位分配结果。"""
    svc = StrategyService(db=db)
    result = svc.run_allocation(strategy_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"策略 {strategy_id} 不存在或不支持资产配置决策管线",
        )
    return result
