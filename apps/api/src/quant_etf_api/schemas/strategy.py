from pydantic import BaseModel


class StrategySummary(BaseModel):
    strategy_id: str
    display_name: str
    version: str
    frequency: str
    asset_scope: str
    description: str


class StrategyDetail(StrategySummary):
    parameter_schema: dict
    required_inputs: list[str]
    factors: list[dict]
    signal_definition: dict
