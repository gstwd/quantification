from __future__ import annotations
from quant_etf_api.plugins.registry import StrategyRegistry, build_default_registry
from quant_etf_api.schemas.strategy import StrategyDetail


class StrategyService:
    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        # 允许外部注入 registry（用于测试或自定义插件集），默认使用内置三个策略
        self.registry = registry or build_default_registry()

    def list_strategies(self) -> list[StrategyDetail]:
        # 将注册表中所有插件的元数据转换为 API 响应格式
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
