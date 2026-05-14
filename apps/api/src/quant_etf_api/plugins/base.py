from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass
class StrategyContextData:
    benchmark_changes: dict[str, float] = field(default_factory=dict)
    share_changes: dict[str, dict[str, float | None]] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


@dataclass
class StrategyResult:
    trade_date: date
    etf_code: str
    strategy_id: str
    signal_score: float
    signal_level: str
    signal_label: str
    factor_values: list[dict] = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


class StrategyPlugin(Protocol):
    strategy_id: str
    display_name: str
    version: str
    frequency: str
    asset_scope: str
    description: str

    def parameter_schema(self) -> dict: ...
    def required_inputs(self) -> list[str]: ...
    def factor_definitions(self) -> list[dict]: ...
    def signal_definition(self) -> dict: ...
    def prepare_context(self, trade_date: date, params: dict | None = None) -> StrategyContextData: ...
    def run_for_universe(self, trade_date: date, universe: list[dict], context: StrategyContextData, params: dict | None = None) -> list[StrategyResult]: ...
    def explain_result(self, result: StrategyResult) -> dict: ...
