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
    - assess_market_timing：市场择时评估
    - rank_assets：资产轮动排名
    - allocate_positions：仓位分配
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

    # ── 可选决策管线方法 ──────────────────────────────────────────────────
    # 以下方法为可选实现，不实现时调用方通过 hasattr 检查后跳过。
    # 新的资产配置策略插件应实现这三个方法，旧插件无需改动。

    def assess_market_timing(
        self,
        trade_date: date,
        context: StrategyContextData,
        params: dict[str, Any] | None = None,
    ) -> TimingSignal | None:
        """市场择时评估（可选实现）。

        综合估值、趋势、量能等指标判断当前市场环境，
        输出 regime（进攻/防守/观望）和 confidence（0-100）。

        不实现时返回 None，表示该策略不包含择时逻辑。
        """
        ...

    def rank_assets(
        self,
        trade_date: date,
        universe: list[dict[str, Any]],
        context: StrategyContextData,
        params: dict[str, Any] | None = None,
    ) -> list[AssetRanking] | None:
        """资产轮动排名（可选实现）。

        按动量 + 估值对 ETF 排名，输出综合得分排序的列表。
        不实现时返回 None，表示该策略不做轮动。
        """
        ...

    def allocate_positions(
        self,
        timing: TimingSignal | None,
        rankings: list[AssetRanking] | None,
        params: dict[str, Any] | None = None,
    ) -> AllocationPlan | None:
        """仓位分配（可选实现）。

        根据择时信号和资产排名，确定每只 ETF 的目标仓位比例。
        不实现时返回 None。
        """
        ...
