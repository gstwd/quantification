from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class FactorRow(BaseModel):
    """因子时间序列单点。

    Attributes:
        trade_date: 交易日。
        index_code: 指数代码。
        factor_id: 因子模板 ID。
        params: 本次计算的规范化参数。
        factor_value_numeric: 因子数值，None 表示数据不足。
        factor_value_text: 因子文本值（枚举类因子使用）。
        factor_payload: 计算中间过程数据，用于调试和解释。
    """

    trade_date: date
    index_code: str
    factor_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    factor_value_numeric: float | None = None
    factor_value_text: str | None = None
    factor_payload: dict[str, Any] = Field(default_factory=dict)
