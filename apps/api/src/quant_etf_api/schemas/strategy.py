from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StrategySummary(BaseModel):
    strategy_id: str
    display_name: str
    version: str
    frequency: str
    asset_scope: str
    description: str


class StrategyDetail(StrategySummary):
    parameter_schema: dict[str, Any]
    required_inputs: list[str]
    factors: list[dict[str, Any]]
    signal_definition: dict[str, Any]


class AllocationResponse(BaseModel):
    """资产配置决策管线响应。

    Attributes:
        timing: 择时信号，含 regime、confidence、label、factors。
        rankings: 资产轮动排名列表。
        plan: 仓位分配方案，含 positions、total_exposure、cash_ratio、reasoning。
    """

    timing: dict[str, Any]
    rankings: list[dict[str, Any]]
    plan: dict[str, Any]
