"""因子层核心抽象：FactorSpec、FactorContext、FactorValue 数据类 + FactorComputer Protocol。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable


@dataclass
class FactorSpec:
    """因子元数据描述符，由 FactorComputer.spec 属性提供。

    Attributes:
        factor_id: 因子唯一标识，如 volume_ratio_20d。
        name: 因子中文名称，如 20日量比。
        category: 因子类别：volume/momentum/volatility/flow/valuation。
        version: 语义化版本号，如 1.0.0。
        description: 计算逻辑说明。
        required_data: 依赖的数据源列表，如 ["index_bars", "index_valuation"]。
    """

    factor_id: str
    name: str
    category: str
    version: str
    description: str
    required_data: list[str] = field(default_factory=list)


@dataclass
class FactorContext:
    """单次计算所需的全部数据视图，由 FactorService._load_context() 构建。

    所有 dict 的 key 均为 (index_code, trade_date) 二元组，
    value 为对应的 ORM 行对象（保留 .volume、.close_price 等属性）。
    回望窗口固定为 90 个自然日，满足 Return60dComputer 需求。

    Attributes:
        index_bars: 指数日线映射，key=(index_code, date)。
        index_valuation: 指数估值映射，key=(index_code, date)，含 pe_percentile/pb_percentile。
    """

    index_bars: dict[tuple[str, date], Any] = field(default_factory=dict)
    index_valuation: dict[tuple[str, date], Any] = field(default_factory=dict)


@dataclass
class FactorValue:
    """单个因子在单个 ETF 上的计算结果。

    Attributes:
        factor_id: 与 FactorSpec.factor_id 一致。
        numeric: 数值型因子结果，None 表示数据不足无法计算。
        text: 文本型因子结果（枚举类因子使用）。
        payload: 计算中间过程数据，用于调试和解释。
    """

    factor_id: str
    numeric: float | None = None
    text: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class FactorComputer(Protocol):
    """因子计算器协议，所有内置/扩展因子只需实现此接口，无需继承任何基类。

    使用结构化子类型（Protocol）而非继承，与 StrategyPlugin 保持风格一致。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回该因子的元数据描述符。"""
        ...

    def compute(
        self,
        index_code: str,
        trade_date: date,
        ctx: FactorContext,
    ) -> FactorValue:
        """计算指定指数在指定交易日的因子值。

        Args:
            index_code: 指数代码，如 000300。
            trade_date: 目标交易日。
            ctx: 包含 90 天回望数据的上下文。

        Returns:
            FactorValue，numeric 为 None 表示数据不足无法计算。
        """
        ...
