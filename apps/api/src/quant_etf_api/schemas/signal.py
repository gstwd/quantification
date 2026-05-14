from datetime import date

from pydantic import BaseModel


class SignalRow(BaseModel):
    trade_date: date
    etf_code: str
    strategy_id: str
    signal_score: float
    signal_level: str
    signal_label: str
    signal_payload: dict = {}


class FactorRow(BaseModel):
    trade_date: date
    etf_code: str
    factor_id: str
    factor_value_numeric: float | None = None
    factor_value_text: str | None = None
    factor_payload: dict = {}
    strategy_id: str | None = None
