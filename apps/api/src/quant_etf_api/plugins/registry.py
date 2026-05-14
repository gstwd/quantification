from dataclasses import asdict

from quant_etf_api.plugins.base import StrategyPlugin
from quant_etf_api.plugins.builtins.share_flow_monitor.plugin import ShareFlowMonitorPlugin
from quant_etf_api.plugins.builtins.three_factor.plugin import ThreeFactorGuardPlugin
from quant_etf_api.plugins.builtins.volume_breakout.plugin import VolumeBreakoutDailyPlugin


class StrategyRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, StrategyPlugin] = {}

    def register(self, plugin: StrategyPlugin) -> None:
        self._plugins[plugin.strategy_id] = plugin

    def all(self) -> list[StrategyPlugin]:
        return list(self._plugins.values())

    def get(self, strategy_id: str) -> StrategyPlugin | None:
        return self._plugins.get(strategy_id)

    def as_summaries(self) -> list[dict]:
        summaries = []
        for plugin in self.all():
            summaries.append(
                {
                    "strategy_id": plugin.strategy_id,
                    "display_name": plugin.display_name,
                    "version": plugin.version,
                    "frequency": plugin.frequency,
                    "asset_scope": plugin.asset_scope,
                    "description": plugin.description,
                    "parameter_schema": plugin.parameter_schema(),
                    "required_inputs": plugin.required_inputs(),
                    "factors": plugin.factor_definitions(),
                    "signal_definition": plugin.signal_definition(),
                }
            )
        return summaries


def build_default_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(ThreeFactorGuardPlugin())
    registry.register(ShareFlowMonitorPlugin())
    registry.register(VolumeBreakoutDailyPlugin())
    return registry
