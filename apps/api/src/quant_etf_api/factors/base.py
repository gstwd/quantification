"""因子层核心抽象：FactorSpec、FactorContext、FactorValue 数据类 + FactorComputer Protocol。"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable


# 因子元数据取值常量（与 factor_definition 表、前端展示共用）。
# 挂载域：因子值挂载的标的类型。本系统策略资产统一为指数
# （benchmark_index）；行业/个股只作为因子计算输入，不产生行业因子域。
ASSET_DOMAIN_INDEX = "index"

# 值形态：决定因子值在实时/回测中的加载与预计算方式。
VALUE_SHAPE_ASSET = "asset"  # 每资产一个独立值（如 return_20d）
VALUE_SHAPE_MARKET = "market"  # 市场级单一值（如市场宽度）

# 适用位置（usage）：因子允许被策略管线中的哪些模块消费。
USAGE_TIMING = "timing"
USAGE_SCORE = "score"
USAGE_FILTER = "filter"
USAGE_RANK = "rank"

# 存量指数因子默认允许的消费位置（保持向后兼容的显式声明）；
# 新因子应在 FactorSpec.usage 中按语义收敛，市场级因子只开放适用位置。
DEFAULT_INDEX_FACTOR_USAGE = [USAGE_TIMING, USAGE_SCORE, USAGE_FILTER, USAGE_RANK]


def factor_params_hash(params: dict[str, Any]) -> str:
    """计算因子参数指纹（规范化 JSON 的 sha256）。

    用于参数化因子值表（index_factor_value / industry_factor_value）区分
    不同参数组合，避免同 factor_id 不同参数互相覆盖。

    Args:
        params: 因子计算参数字典。

    Returns:
        64 位十六进制 sha256 哈希。
    """
    canonical = json.dumps(
        params,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        market_scope: 是否需要在全市场指数范围上计算（如市场宽度类因子）。
            为 True 时，回测服务会额外加载全市场行情数据作为因子上下文，
            保证实时预计算（全市场）与回测（策略池 + 全市场补充）口径一致。
        asset_domain: 因子值挂载的标的类型，本系统统一为 index
            （benchmark_index）；该轴不再作为“策略资产域”参与校验。
        value_shape: 因子值形态：asset=每资产值、market=市场级值、
            每资产可配置因子必须是 asset/market 形态（行业面板等只作
            因子内部数据依赖，不作为可配置因子）。
        usage: 适用位置数组：timing/score/filter/rank，
            配置校验按此限制因子在策略中的消费位置。
        default_params: 因子默认参数（参数化因子的默认口径，非参数化因子为空）。
    """

    factor_id: str
    name: str
    category: str
    version: str
    description: str
    required_data: list[str] = field(default_factory=list)
    lookback_days: int = 90
    market_scope: bool = False
    asset_domain: str = ASSET_DOMAIN_INDEX
    value_shape: str = VALUE_SHAPE_ASSET
    usage: list[str] = field(default_factory=lambda: list(DEFAULT_INDEX_FACTOR_USAGE))
    default_params: dict[str, Any] = field(default_factory=dict)


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
        panels: 复合因子数据面板（按 required_data 名称注入），如
            index_membership/stock_closes/industry_selection/
            index_industry_exposure，由实时与回测两侧的装配层填充。
    """

    index_bars: dict[tuple[str, date], Any] = field(default_factory=dict)
    index_valuation: dict[tuple[str, date], Any] = field(default_factory=dict)
    macro_indicators: dict[str, dict[str, float]] = field(default_factory=dict)
    panels: dict[str, Any] = field(default_factory=dict)


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
