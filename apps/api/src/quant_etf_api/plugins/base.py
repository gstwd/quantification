"""策略插件基础设施：StrategyPlugin Protocol + 领域模型 re-export。

StrategyContextData、StrategyResult、TimingSignal、AssetRanking、AllocationPlan
已迁移至 domain.strategies.models，此处保留 re-export 以保持现有 import 路径兼容。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from quant_etf_api.domain.strategies.models import (
    AllocationPlan,
    AssetRanking,
    StrategyContextData,
    StrategyResult,
    TimingSignal,
)

# re-export 领域模型，保持 from quant_etf_api.plugins.base import ... 可用
__all__ = [
    "AllocationPlan",
    "AssetRanking",
    "StrategyContextData",
    "StrategyPlugin",
    "StrategyResult",
    "TimingSignal",
]


class StrategyPlugin(Protocol):
    """策略插件协议（结构化子类型），所有插件无需继承，只需实现以下属性和方法。

    必须实现的属性和方法：元数据属性 + parameter_schema / required_inputs /
    factor_definitions / signal_definition / prepare_context / run_for_universe / explain_result。

    可选实现的决策管线方法（通过 hasattr 检查）：
    - assess_market_timing(trade_date, context, params) -> TimingSignal
        市场择时评估，综合估值/趋势/量能判断市场环境，输出 regime + confidence。
    - rank_assets(trade_date, universe, context, params) -> list[AssetRanking]
        资产轮动排名，按动量 + 估值对标的排名，输出综合得分排序列表。
    - allocate_positions(timing, rankings, params) -> AllocationPlan
        仓位分配，根据择时信号和资产排名确定目标仓位比例。

    新的资产配置策略插件应实现这三个方法，旧插件无需改动。
    StrategyRegistry.has_decision_pipeline() 封装了 hasattr 检查。
    """

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
