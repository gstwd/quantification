from fastapi import APIRouter, HTTPException

from quant_etf_api.schemas.strategy import StrategyDetail
from quant_etf_api.services.strategy_service import StrategyService

router = APIRouter(tags=["strategies"])
service = StrategyService()


@router.get("/strategies", response_model=list[StrategyDetail])
def list_strategies() -> list[StrategyDetail]:
    return service.list_strategies()


@router.get("/strategies/{strategy_id}", response_model=StrategyDetail)
def get_strategy(strategy_id: str) -> StrategyDetail:
    strategy = service.get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy
