"""站内告警接口模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from quant_etf_api.schemas.types import UtcDatetime


class AlertItem(BaseModel):
    """一条站内告警。"""

    id: int
    kind: str
    severity: str
    strategy_id: str | None = None
    title: str
    detail: dict[str, Any] | None = None
    created_at: UtcDatetime | None = None
    read_at: UtcDatetime | None = None


class AlertListResponse(BaseModel):
    """告警列表与未读数。"""

    items: list[AlertItem]
    unread_count: int


class AlertReadAllResponse(BaseModel):
    """全部已读的结果。"""

    updated: int
