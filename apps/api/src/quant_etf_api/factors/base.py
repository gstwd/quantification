"""因子层核心抽象：FactorSpec、FactorContext、FactorValue 数据类 + FactorComputer Protocol。"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable


class MissingReason(str, Enum):
    """因子值缺失原因（三态语义，对应层间协作问题 C2）。

    引擎层区分三种缺失，避免全部退化为 None 后无法判断根因：
    - FACTOR_UNKNOWN：因子 ID 未注册（配置错误，校验期应快速失败）
    - NOT_COMPUTED：因子已注册但当日未计算（调度缺失，可补算）
    - INSUFFICIENT_DATA：因子已计算但数值为 NULL（基础行情不足）
    """

    FACTOR_UNKNOWN = "factor_unknown"
    NOT_COMPUTED = "not_computed"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class FactorSpec:
    """因子元数据描述符，由 FactorComputer.spec 属性提供。

    Attributes:
        factor_id: 因子唯一标识，如 volume_ratio_20d。
        name: 因子中文名称，如 20日量比。
        category: 因子类别：volume/momentum/volatility/flow/valuation/technical。
        version: 语义化版本号，如 1.0.0。
        description: 计算逻辑说明。
        required_data: 依赖的数据源列表，如 ["index_bars", "index_valuation"]。
        lookback_days: 因子计算所需的自然日回望窗口，默认 90 天。
    """

    factor_id: str
    name: str
    category: str
    version: str
    description: str
    required_data: list[str] = field(default_factory=list)
    lookback_days: int = 90


@dataclass
class FactorContext:
    """单次计算所需的全部数据视图，由 FactorService._load_context() 构建。

    所有 dict 的 key 均为 (index_code, trade_date) 二元组，
    value 为对应的 ORM 行对象（保留 .volume、.close_price 等属性）。
    回望窗口由各因子的 FactorSpec.lookback_days 最大值动态决定。

    Attributes:
        index_bars: 指数日线映射，key=(index_code, date)。
        index_valuation: 指数估值映射，key=(index_code, date)，含 pe_percentile/pb_percentile。
        macro_indicators: 宏观指标映射，key=indicator_code，value={period_date: value}。
        ai_sentiment: AI 情绪聚合数据映射，key=(asset_tag, date)，value=DailySentimentAggregate ORM 行。
    """

    index_bars: dict[tuple[str, date], Any] = field(default_factory=dict)
    index_valuation: dict[tuple[str, date], Any] = field(default_factory=dict)
    macro_indicators: dict[str, dict[str, float]] = field(default_factory=dict)
    ai_sentiment: dict[tuple[str, date], Any] = field(default_factory=dict)


@dataclass
class FactorValue:
    """单个因子在单个指数上的计算结果。

    Attributes:
        factor_id: 与 FactorSpec.factor_id 一致。
        numeric: 数值型因子结果，None 表示数据不足无法计算。
        text: 文本型因子结果（枚举类因子使用）。
        payload: 计算中间过程数据，用于调试和解释。
        missing_reason: 缺失原因（三态语义），numeric 为 None 时由加载侧填充。
    """

    factor_id: str
    numeric: float | None = None
    text: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    missing_reason: MissingReason | None = None


@runtime_checkable
class FactorComputer(Protocol):
    """因子计算器协议，所有内置/扩展因子只需实现此接口，无需继承任何基类。

    使用结构化子类型（Protocol）而非继承。
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
            ctx: 包含回望数据的上下文。

        Returns:
            FactorValue，numeric 为 None 表示数据不足无法计算。
        """
        ...


@runtime_checkable
class BatchFactorComputer(Protocol):
    """支持批量计算的扩展因子协议（可选实现，用于回测性能优化）。

    实现此协议的因子在回测预计算阶段会被优先使用，
    一次调用覆盖所有交易日，避免对每个日期重复遍历全量 bar 数据。
    不实现此协议的因子退回逐点调用（向后兼容）。
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
        """逐点计算（保持向后兼容）。"""
        ...

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算多个交易日的因子值。

        Args:
            index_code: 指数代码，如 000300。
            dates: 需要计算的交易日列表（升序）。
            ctx: 包含全量回望数据的上下文（通常覆盖整个回测区间）。

        Returns:
            key=交易日, value=FactorValue 的字典，不包含无数据的日期。
        """
        ...
