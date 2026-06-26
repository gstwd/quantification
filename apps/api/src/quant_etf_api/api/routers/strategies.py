"""策略路由：策略列表、详情、配置管理和资产配置决策管线。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.strategy import (
    AllocationResponse,
    StrategyConfigCreate,
    StrategyConfigUpdate,
    StrategyDetail,
    StrategySummary,
    StrategyValidationResult,
)
from quant_etf_api.services.strategy_service import StrategyService

router = APIRouter(tags=["strategies"])


@router.get("/strategies", response_model=list[StrategySummary])
def list_strategies(db: Session = Depends(get_db)) -> list[StrategySummary]:
    """返回所有已启用策略的摘要列表。"""
    return StrategyService(db).list_strategies()


@router.get("/strategies/{strategy_id}", response_model=StrategyDetail)
def get_strategy(strategy_id: str, db: Session = Depends(get_db)) -> StrategyDetail:
    """按 ID 获取策略详情。"""
    strategy = StrategyService(db).get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return strategy


@router.post("/strategies", response_model=StrategyDetail, status_code=201)
def create_strategy(req: StrategyConfigCreate, db: Session = Depends(get_db)) -> StrategyDetail:
    """创建策略配置。"""
    try:
        return StrategyService(db).create_config(req)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/strategies/{strategy_id}", response_model=StrategyDetail)
def update_strategy(
    strategy_id: str, req: StrategyConfigUpdate, db: Session = Depends(get_db)
) -> StrategyDetail:
    """更新策略配置。"""
    try:
        result = StrategyService(db).update_config(strategy_id, req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return result


@router.delete("/strategies/{strategy_id}", status_code=204)
def delete_strategy(strategy_id: str, db: Session = Depends(get_db)) -> None:
    """删除策略配置。"""
    if not StrategyService(db).delete_config(strategy_id):
        raise HTTPException(status_code=404, detail="策略不存在")


@router.post("/strategies/validate", response_model=StrategyValidationResult)
def validate_strategy_config(
    config_json: dict, db: Session = Depends(get_db)
) -> StrategyValidationResult:
    """校验策略配置 JSON 是否合法（不持久化）。"""
    return StrategyService(db).validate_config(config_json)


@router.get("/strategies/{strategy_id}/allocation", response_model=AllocationResponse)
def run_allocation(
    strategy_id: str,
    trade_date: date | None = Query(None, description="指定交易日（YYYY-MM-DD），不传则使用最新数据"),
    db: Session = Depends(get_db),
) -> AllocationResponse:
    """运行资产配置决策管线，返回择时、排名、仓位分配结果。

    支持通过 trade_date 参数查看历史某一天的决策结果。
    """
    result = StrategyService(db).run_allocation(strategy_id, trade_date=trade_date)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"策略 {strategy_id} 不存在或不支持资产配置决策管线",
        )
    return result
