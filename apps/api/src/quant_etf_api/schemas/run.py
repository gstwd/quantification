from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from quant_etf_api.schemas.types import UtcDatetime


class ResearchRunSummary(BaseModel):
    """运行记录列表摘要，用于列表页展示。"""

    run_id: str
    run_type: str
    strategy_id: str | None = None
    trade_date: date | None = None
    status: str
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error_message: str | None = None


class ResearchRunDetail(BaseModel):
    """运行记录详情，包含完整指标和耗时。"""

    run_id: str
    run_type: str
    strategy_id: str | None = None
    trade_date: date | None = None
    status: str
    params: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    duration_seconds: float | None = None


class ResearchRunItemSchema(BaseModel):
    """单条运行明细，对应一个指数标的或一个子任务的处理结果。"""

    id: int
    run_id: str
    index_code: str
    status: str
    message: str | None = None
    metrics: dict[str, Any] | None = None
