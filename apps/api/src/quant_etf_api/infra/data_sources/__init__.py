"""数据源多源管理框架。

提供：
- CircuitBreaker：熔断器，管理数据源健康状态
- BaseDataSourceAdapter：数据源适配器抽象基类
- SourceCapability：能力声明数据类
- DataSourceManager：多源管理器，优先级编排 + 故障切换
- 异常层级：DataSourceError / DataSourceUnavailableError / RateLimitError
"""

from __future__ import annotations

from quant_etf_api.infra.data_sources.circuit_breaker import CircuitBreaker
from quant_etf_api.infra.data_sources.base import (
    BaseDataSourceAdapter,
    DataSourceError,
    DataSourceUnavailableError,
    RateLimitError,
    SourceCapability,
)

__all__ = [
    "BaseDataSourceAdapter",
    "CircuitBreaker",
    "DataSourceError",
    "DataSourceUnavailableError",
    "RateLimitError",
    "SourceCapability",
]
