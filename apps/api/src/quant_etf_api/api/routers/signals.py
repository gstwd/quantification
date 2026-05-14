from datetime import date

from fastapi import APIRouter, Query

from quant_etf_api.schemas.signal import FactorRow, SignalRow
from quant_etf_api.services.signal_service import SignalService

router = APIRouter(tags=["signals"])
service = SignalService()


@router.get("/signals/latest", response_model=list[SignalRow])
def latest_signals(strategy_id: str = Query(...)) -> list[SignalRow]:
    return service.latest_signals(strategy_id)


@router.get("/signals/history", response_model=list[SignalRow])
def signal_history(strategy_id: str = Query(...), etf_code: str = Query(...)) -> list[SignalRow]:
    return [row for row in service.latest_signals(strategy_id) if row.etf_code == etf_code]


@router.get("/factors", response_model=list[FactorRow])
def factor_rows(etf_code: str = Query(...), trade_date: date = Query(...)) -> list[FactorRow]:
    return service.factor_rows(etf_code, trade_date)
