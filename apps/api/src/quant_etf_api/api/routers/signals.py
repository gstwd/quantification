from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import date

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.signal import FactorRow, SignalRow
from quant_etf_api.services.signal_service import SignalService

router = APIRouter(tags=["signals"])


@router.get("/signals/latest", response_model=list[SignalRow])
def latest_signals(strategy_id: str = Query(...), db: Session = Depends(get_db)) -> list[SignalRow]:
    # 返回指定策略最新交易日的所有 ETF 信号，按得分降序
    return SignalService(db).latest_signals(strategy_id)


@router.get("/signals/history", response_model=list[SignalRow])
def signal_history(strategy_id: str = Query(...), etf_code: str = Query(...), db: Session = Depends(get_db)) -> list[SignalRow]:
    # 当前实现复用 latest_signals 并在内存中过滤，后续可改为按 ETF 查历史
    return [row for row in SignalService(db).latest_signals(strategy_id) if row.etf_code == etf_code]


@router.get("/factors", response_model=list[FactorRow])
def factor_rows(etf_code: str = Query(...), trade_date: date = Query(...), db: Session = Depends(get_db)) -> list[FactorRow]:
    return SignalService(db).factor_rows(etf_code, trade_date)
