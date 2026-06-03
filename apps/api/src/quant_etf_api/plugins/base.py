"""策略插件基础设施：StrategyPlugin Protocol + 领域模型 re-export。

StrategyContextData 和 StrategyResult 已迁移至 domain.strategies.models，
此处保留 re-export 以保持现有 import 路径兼容。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from quant_etf_api.domain.strategies.models import StrategyContextData, StrategyResult

# re-export 领域模型，保持 from quant_etf_api.plugins.base import ... 可用
__all__ = ["StrategyContextData", "StrategyPlugin", "StrategyResult"]


class StrategyPlugin(Protocol):
    """策略插件协议（结构化子类型），所有插件无需继承，只需实现以下属性和方法。"""

    strategy_id: str
    display_name: str
    version: str
    frequency: str
    asset_scope: str
    description: str

    def parameter_schema(self) -> dict[str, Any]: ...
    def required_inputs(self) -> list[str]: ...
    def factor_definitions(self) -> list[dict[str, Any]]: ...
    def signal_definition(self) -> dict[str, Any]: ...
    def prepare_context(
        self, trade_date: date, params: dict[str, Any] | None = None
    ) -> StrategyContextData: ...
    def run_for_universe(
        self,
        trade_date: date,
        universe: list[dict[str, Any]],
        context: StrategyContextData,
        params: dict[str, Any] | None = None,
    ) -> list[StrategyResult]: ...
    def explain_result(self, result: StrategyResult) -> dict[str, Any]: ...
