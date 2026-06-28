"""领域枚举常量。"""

from __future__ import annotations

from enum import StrEnum


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
    FAILED = "failed"


class RunType(StrEnum):
    """研究运行类型。"""

    DAILY_INGEST = "daily_ingest"
    STRATEGY_RUN = "strategy_run"
    UNIVERSE_REFRESH = "universe_refresh"
    COLD_START = "cold_start"
    STARTUP_FILL = "startup_fill"
    FACTOR_COMPUTATION = "factor_computation"


class FactorCategory(StrEnum):
    """因子类别。"""

    VOLUME = "volume"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    FLOW = "flow"
    VALUATION = "valuation"
    SENTIMENT = "sentiment"
    ATTENTION = "attention"
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
