from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel


class FactorRow(BaseModel):
    """因子值行，用于时间序列和横截面查询响应。"""

    trade_date: date
    index_code: str
    factor_id: str
    factor_value_numeric: float | None = None
    factor_value_text: str | None = None
    factor_payload: dict[str, Any] = {}
    strategy_id: str | None = None
