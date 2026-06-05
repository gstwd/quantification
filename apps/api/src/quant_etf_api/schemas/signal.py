from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel


class SignalRow(BaseModel):
    trade_date: date
    etf_code: str
    strategy_id: str
    signal_score: float
    signal_level: str
    signal_label: str
    signal_payload: dict[str, Any] = {}


class FactorRow(BaseModel):
    """因子值行，用于时间序列和横截面查询响应。"""

    trade_date: date
    index_code: str
    factor_id: str
    factor_value_numeric: float | None = None
    factor_value_text: str | None = None
    factor_payload: dict[str, Any] = {}
    strategy_id: str | None = None
