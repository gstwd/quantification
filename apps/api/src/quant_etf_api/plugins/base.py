from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass
class StrategyContextData:
    # 基准指数当日涨跌幅，key=指数代码，value=涨跌幅（%）
    benchmark_changes: dict[str, float] = field(default_factory=dict)
    # ETF 份额变化数据，key=ETF 代码，value=包含 share_delta_pct 等字段的字典
    share_changes: dict[str, dict[str, float | None]] = field(default_factory=dict)
    # 扩展字段，各插件可自定义存放额外上下文数据
    extra: dict = field(default_factory=dict)


@dataclass
class StrategyResult:
    trade_date: date
    etf_code: str
    strategy_id: str
    signal_score: float        # 综合得分，0-100
    signal_level: str          # 信号等级：HIGH / MID / LOW
    signal_label: str          # 信号中文标签：高确信 / 中等关注 / 正常
    factor_values: list[dict] = field(default_factory=list)   # 各因子计算结果列表
    payload: dict = field(default_factory=dict)               # 计算中间数据，用于解释和调试
    tags: list[str] = field(default_factory=list)             # 标签，如跟踪指数名称


class StrategyPlugin(Protocol):
    """策略插件协议（结构化子类型），所有插件无需继承，只需实现以下属性和方法。"""
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
