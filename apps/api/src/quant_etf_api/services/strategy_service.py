from quant_etf_api.plugins.registry import StrategyRegistry, build_default_registry
from quant_etf_api.schemas.strategy import StrategyDetail


class StrategyService:
    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self.registry = registry or build_default_registry()

    def list_strategies(self) -> list[StrategyDetail]:
        return [StrategyDetail(**item) for item in self.registry.as_summaries()]

    def get_strategy(self, strategy_id: str) -> StrategyDetail | None:
        plugin = self.registry.get(strategy_id)
        if plugin is None:
            return None
        return StrategyDetail(
            strategy_id=plugin.strategy_id,
            display_name=plugin.display_name,
            version=plugin.version,
            frequency=plugin.frequency,
            asset_scope=plugin.asset_scope,
            description=plugin.description,
            parameter_schema=plugin.parameter_schema(),
            required_inputs=plugin.required_inputs(),
            factors=plugin.factor_definitions(),
            signal_definition=plugin.signal_definition(),
        )
