"""策略领域模型（纯领域逻辑，无外部依赖）。

定义策略执行的核心数据结构：
- StrategyContextData：策略执行上下文，包含基准指数、份额变化等数据
- StrategyResult：单只 ETF 的策略执行结果，包含信号得分、因子值等
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class StrategyContextData:
    """策略执行上下文，由服务层构建后传入插件。

    Attributes:
        benchmark_changes: 基准指数当日涨跌幅，key=指数代码，value=涨跌幅（%）。
        share_changes: ETF 份额变化数据，key=ETF 代码，value=包含 share_delta_pct 等字段的字典。
        extra: 扩展字段，各插件可自定义存放额外上下文数据。
    """

    benchmark_changes: dict[str, float] = field(default_factory=dict)
    share_changes: dict[str, dict[str, float | None]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyResult:
    """单只 ETF 的策略执行结果。

    Attributes:
        trade_date: 交易日。
        etf_code: ETF 代码。
        strategy_id: 策略标识。
        signal_score: 综合得分，0-100。
        signal_level: 信号等级：HIGH / MID / LOW。
        signal_label: 信号中文标签：高确信 / 中等关注 / 正常。
        factor_values: 各因子计算结果列表。
        payload: 计算中间数据，用于解释和调试。
        tags: 标签，如跟踪指数名称。
    """

    trade_date: date
    etf_code: str
    strategy_id: str
    signal_score: float
    signal_level: str
    signal_label: str
    factor_values: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
