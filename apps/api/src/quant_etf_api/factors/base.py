"""因子层核心抽象：FactorSpec、FactorContext、FactorValue 数据类 + FactorComputer Protocol。"""

from __future__ import annotations

import bisect
from collections.abc import Callable
from enum import Enum
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol, runtime_checkable


# 值形态：决定因子值在实时/回测中的加载与计算方式。
VALUE_SHAPE_ASSET = "asset"  # 每资产一个独立值（如 return_20d）
VALUE_SHAPE_MARKET = "market"  # 市场级单一值（如市场宽度）
ValueShape = Literal["asset", "market"]

# 适用位置（usage）：因子允许被策略管线中的哪些模块消费。
USAGE_TIMING = "timing"
USAGE_SCORE = "score"
USAGE_FILTER = "filter"
USAGE_RANK = "rank"

# 存量指数因子默认允许的消费位置（保持向后兼容的显式声明）；
# 新因子应在 FactorSpec.usage 中按语义收敛，市场级因子只开放适用位置。
DEFAULT_INDEX_FACTOR_USAGE = [USAGE_TIMING, USAGE_SCORE, USAGE_FILTER, USAGE_RANK]

# 交易日周期折算自然日回望窗口的系数与安全余量：
# A 股一年约 244 个交易日（≈1.5 倍自然日），叠加春节/国庆连续休市后，
# 单周期窗口按 1.6 倍并留 10 天缓冲，避免 21 个交易日跨两个长假时窗口不足。
_LOOKBACK_FACTOR = 1.6
_LOOKBACK_PADDING_DAYS = 10
_MIN_LOOKBACK_DAYS = 15


def period_lookback_days(period: int, *, factor: float = _LOOKBACK_FACTOR) -> int:
    """按交易日周期推导回望自然日数（技术指标通用口径）。

    Args:
        period: 交易日周期，如 5/20/63。
        factor: 交易日 → 自然日的折算系数，长周期需要更大余量时显式放大。

    Returns:
        回望自然日数，至少 ``_MIN_LOOKBACK_DAYS`` 天。
    """
    return max(_MIN_LOOKBACK_DAYS, int(period * factor) + _LOOKBACK_PADDING_DAYS)


class MissingReason(str, Enum):
    """因子值缺失原因（三态语义）。

    引擎层区分三种缺失，避免全部退化为 None 后无法判断根因：
    - TEMPLATE_UNKNOWN：因子引用无法解析为模板实例（配置错误，校验期应快速失败）
    - COMPUTE_FAILED：计算过程抛出异常（实现或数据异常，需查日志）
    - INSUFFICIENT_DATA：计算正常返回 None（基础行情不足）
    """

    TEMPLATE_UNKNOWN = "template_unknown"
    COMPUTE_FAILED = "compute_failed"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class FactorSpec:
    """因子元数据描述符，由 FactorComputer.spec 属性提供。

    Attributes:
        factor_id: 因子标识。模板口径下是模板 ID（如 sma）；计算器自述口径下
            是内部标签（如 ma_20d），不参与对外标识。
        name: 因子中文名称，如 20日量比。
        category: 因子类别：volume/momentum/volatility/flow/valuation/technical。
        version: 语义化版本号，如 1.0.0。
        description: 计算逻辑说明。
        required_data: 依赖的数据源列表，如 ["index_bars", "index_valuation"]。
        lookback_days: 因子计算所需的自然日回望窗口，默认 90 天。
        market_scope: 是否需要在全市场指数范围上计算（如市场宽度类因子）。
            为 True 时，上下文构建会额外加载全市场行情数据，
            保证实时与回测口径一致。
        value_shape: 因子值形态：asset=每资产值、market=市场级值、
            每资产可配置因子必须是 asset/market 形态（行业面板等只作
            因子内部数据依赖，不作为可配置因子）。
        usage: 适用位置数组：timing/score/filter/rank，
            配置校验按此限制因子在策略中的消费位置。
        default_params: 因子默认参数（模板固有口径，零参数因子为空）。
    """

    factor_id: str
    name: str
    category: str
    version: str
    description: str
    required_data: list[str] = field(default_factory=list)
    lookback_days: int = 90
    market_scope: bool = False
    value_shape: ValueShape = VALUE_SHAPE_ASSET
    usage: list[str] = field(default_factory=lambda: list(DEFAULT_INDEX_FACTOR_USAGE))
    default_params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验因子值形态仅允许每资产值或市场级值。"""
        if self.value_shape not in (VALUE_SHAPE_ASSET, VALUE_SHAPE_MARKET):
            raise ValueError("value_shape 必须是 asset 或 market")


@dataclass
class FactorContext:
    """单次计算所需的全部数据视图，由 FactorComputeService 构建。

    所有 dict 的 key 均为 (index_code, trade_date) 二元组，
    value 为对应的 ORM 行对象（保留 .volume、.close_price 等属性）。
    回望窗口由本次实际因子实例的最大 lookback 动态决定。

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
    # 按指数分组的有序序列缓存：同一上下文内多个计算器、多个指数共享，
    # 避免每个 (指数, 因子) 都全量扫描 index_bars。上下文装配完成后不应再修改原始映射。
    _cache: dict[Any, Any] = field(default_factory=dict, init=False, repr=False, compare=False)

    def memo(self, key: tuple[Any, ...], factory: Callable[[], Any]) -> Any:
        """按键缓存派生数据（同一上下文内共享），首次访问时调用 factory 计算。

        供计算器缓存按指数派生的序列（如月线所需的完整 OHLC），键需带上
        计算器专属前缀，避免与内置缓存冲突。

        Args:
            key: 缓存键。
            factory: 无参构造函数。

        Returns:
            缓存值。
        """
        cache_key = ("memo", *key)
        if cache_key not in self._cache:
            self._cache[cache_key] = factory()
        return self._cache[cache_key]

    def _grouped(self, source: str) -> dict[str, list[tuple[date, Any]]]:
        """把 (index_code, date) 键的原始映射按指数分组并按日期升序排序（结果缓存）。

        Args:
            source: 字段名，index_bars 或 index_valuation。

        Returns:
            {index_code: [(date, row), ...]}，每个列表按日期升序。
        """
        key = ("grouped", source)
        grouped = self._cache.get(key)
        if grouped is None:
            grouped = {}
            for (code, row_date), row in getattr(self, source).items():
                grouped.setdefault(code, []).append((row_date, row))
            for rows in grouped.values():
                rows.sort(key=lambda item: item[0])
            self._cache[key] = grouped
        return grouped

    def bar_codes(self) -> list[str]:
        """返回上下文日线中出现的全部指数代码（升序）。"""
        return sorted(self._grouped("index_bars"))

    def bars_for(self, index_code: str) -> tuple[list[date], list[Any]]:
        """返回指定指数按日期升序的全部日线（不过滤缺失字段）。

        Args:
            index_code: 指数代码。

        Returns:
            (dates, bars) 二元组，两者等长且按日期升序。
        """
        return self._split("bars", index_code, "index_bars")

    def valuation_for(self, index_code: str) -> tuple[list[date], list[Any]]:
        """返回指定指数按日期升序的全部估值行。

        Args:
            index_code: 指数代码。

        Returns:
            (dates, rows) 二元组，两者等长且按日期升序。
        """
        return self._split("valuation", index_code, "index_valuation")

    def _split(self, kind: str, index_code: str, source: str) -> tuple[list[date], list[Any]]:
        """把分组序列拆成日期列表与行列表（结果缓存）。"""
        key = (kind, index_code)
        cached = self._cache.get(key)
        if cached is None:
            rows = self._grouped(source).get(index_code) or []
            cached = ([row_date for row_date, _ in rows], [row for _, row in rows])
            self._cache[key] = cached
        return cached

    def series(
        self, index_code: str, fields: tuple[str, ...]
    ) -> tuple[list[date], list[tuple[Any, ...]]]:
        """返回指定指数中给定字段全部非空的日线序列（结果缓存）。

        Args:
            index_code: 指数代码。
            fields: 需要的日线字段，如 ("close_price",) 或 ("high_price", "low_price")。

        Returns:
            (dates, values) 二元组；values 每项是按 fields 顺序的取值元组。
        """
        key = ("series", index_code, fields)
        cached = self._cache.get(key)
        if cached is None:
            dates_out: list[date] = []
            values_out: list[tuple[Any, ...]] = []
            for bar_date, bar in zip(*self.bars_for(index_code)):
                values = tuple(getattr(bar, name, None) for name in fields)
                if any(value is None for value in values):
                    continue
                dates_out.append(bar_date)
                values_out.append(values)
            cached = (dates_out, values_out)
            self._cache[key] = cached
        return cached

    def closes(self, index_code: str) -> tuple[list[date], list[float]]:
        """返回指定指数收盘价非空的有序收盘价序列（结果缓存）。

        Args:
            index_code: 指数代码。

        Returns:
            (dates, closes) 二元组，按日期升序。
        """
        key = ("closes", index_code)
        cached = self._cache.get(key)
        if cached is None:
            dates_out, values = self.series(index_code, ("close_price",))
            cached = (dates_out, [value[0] for value in values])
            self._cache[key] = cached
        return cached

    def macro_series(self, indicator_code: str) -> list[tuple[date, date, str, float]]:
        """返回宏观指标按「可见日」升序的记录（结果缓存）。

        可见日按指标发布滞后推导（见 macro_period.macro_available_date），
        调用方据此二分取「截止 trade_date 已公布」的记录，避免前视偏差。

        Args:
            indicator_code: 宏观指标代码，如 pmi / lpr1y。

        Returns:
            [(可见日, 参考期日期, period 原文, 数值), ...]，按可见日、参考期升序。
        """
        key = ("macro", indicator_code)
        cached = self._cache.get(key)
        if cached is None:
            from quant_etf_api.factors.macro_period import (
                macro_available_date,
                parse_macro_period,
            )

            cached = []
            for period, value in (self.macro_indicators.get(indicator_code) or {}).items():
                period_date = parse_macro_period(period)
                available = macro_available_date(indicator_code, period)
                if period_date is None or available is None or value is None:
                    continue
                cached.append((available, period_date, period, float(value)))
            cached.sort(key=lambda item: (item[0], item[1]))
            self._cache[key] = cached
        return cached


def index_on(dates: list[date], trade_date: date) -> int | None:
    """在升序日期列表中定位 trade_date 的下标，未精确命中时返回 None。

    Args:
        dates: 升序日期列表。
        trade_date: 目标日期。

    Returns:
        trade_date 在列表中的下标；列表中没有该日期时为 None。
    """
    idx = bisect.bisect_right(dates, trade_date) - 1
    if idx < 0 or dates[idx] != trade_date:
        return None
    return idx


@dataclass
class FactorValue:
    """单个因子在单个指数上的计算结果。

    Attributes:
        factor_id: 计算器自述的因子标识（内部标签，用于日志与 payload）；
            对外标识由因子模板与策略别名决定。
        numeric: 数值型因子结果，None 表示数据不足无法计算。
        text: 文本型因子结果（枚举类因子使用）。
        payload: 计算中间过程数据，用于调试和解释。
        missing_reason: 缺失原因（三态语义），numeric 为 None 时由调用侧填充。
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
