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


class BacktestStatus(StrEnum):
    """回测状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
