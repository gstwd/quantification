"""领域枚举常量。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, get_args

# 回测执行模型取值（单一口径来源）：T 日信号在 T+1 开盘成交 / T+1 收盘成交。
# CLI 的 --execution-model、API 请求体与回测落库的 params["_execution_model"]
# 必须解析到同一组取值，否则同一条策略会在不同执行口径下产出不可比较的指标。
ExecutionModel = Literal["t_plus_1_open", "t_plus_1_close"]
DEFAULT_EXECUTION_MODEL: ExecutionModel = "t_plus_1_open"
EXECUTION_MODELS: tuple[str, ...] = get_args(ExecutionModel)


def parse_execution_model(value: str) -> ExecutionModel:
    """校验并归一化执行模型取值。

    Args:
        value: 执行模型字符串（CLI 参数或 API 透传值）。

    Returns:
        合法的执行模型字面量。

    Raises:
        ValueError: 取值不在 ``EXECUTION_MODELS`` 内时抛出（附带可选值说明）。
    """
    if value not in EXECUTION_MODELS:
        raise ValueError(
            f"不支持的执行模型：{value}，可选值为 {', '.join(EXECUTION_MODELS)}"
        )
    return value  # type: ignore[return-value]


class SignalLevel(StrEnum):
    """信号等级。"""

    HIGH = "HIGH"
    MID = "MID"
    LOW = "LOW"


class RunStatus(StrEnum):
    """研究运行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    SKIPPED = "skipped"
    FAILED = "failed"


class RunType(StrEnum):
    """研究运行类型。"""

    DAILY_INGEST = "daily_ingest"
    STRATEGY_RUN = "strategy_run"
    COLD_START = "cold_start"
    INDEX_REBUILD = "index_rebuild"
    INDEX_INCREMENTAL_FILL = "index_incremental_fill"


class FactorCategory(StrEnum):
    """因子类别。"""

    VOLUME = "volume"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    FLOW = "flow"
    VALUATION = "valuation"
    FUNDAMENTAL = "fundamental"


class BacktestStatus(StrEnum):
    """回测状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class MarketPhase(StrEnum):
    """市场阶段枚举。"""

    TRENDING_UP = "trending_up"  # 趋势上涨
    TRENDING_DOWN = "trending_down"  # 趋势下跌
    RANGING = "ranging"  # 震荡
    ROTATION = "rotation"  # 风格/板块轮动
    EUPHORIA = "euphoria"  # 情绪高潮
    PANIC = "panic"  # 恐慌
    REPAIR = "repair"  # 修复


class SizeStyle(StrEnum):
    """大小盘风格枚举。"""

    LARGE_CAP = "large_cap"
    SMALL_CAP = "small_cap"
    BALANCED = "balanced"


class GrowthStyle(StrEnum):
    """成长/价值风格枚举。"""

    GROWTH = "growth"
    VALUE = "value"
    BALANCED = "balanced"


class SectorLeading(StrEnum):
    """行业主导方向枚举。"""

    TECH = "tech"
    DIVIDEND = "dividend"
    CYCLICAL = "cyclical"
    FINANCIAL = "financial"
    CONSUMPTION = "consumption"
    HEALTHCARE = "healthcare"
    BALANCED = "balanced"
