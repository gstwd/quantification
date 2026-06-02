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
