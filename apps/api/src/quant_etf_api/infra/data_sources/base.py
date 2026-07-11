"""数据源适配器抽象基类。

定义了所有数据源适配器需要遵循的统一接口和能力声明机制。
参考 DSA 项目的 BaseFetcher + DataFetcherManager 模式，结合本项目的
BaseDataClient 风格。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from quant_etf_api.infra.clients.base import HealthStatus


# ---- 异常层级 ----

class DataSourceError(Exception):
    """数据源通用异常基类。"""


class DataSourceUnavailableError(DataSourceError):
    """数据源不可用（未配置或暂时不可达）。"""


class RateLimitError(DataSourceError):
    """数据源触发速率限制。"""


# ---- 能力声明 ----

@dataclass
class SourceCapability:
    """数据源能力描述。

    声明该适配器支持哪些数据类型的获取，以及覆盖哪些市场。
    DataSourceManager 据此做路由决策。

    Attributes:
        supports_etf_kline: 是否支持 ETF K 线（OHLCV）数据。
        supports_etf_shares: 是否支持 ETF 份额/规模数据。
        supports_index_daily: 是否支持指数日线行情。
        supports_index_valuation: 是否支持指数估值（PE/PB 及分位）。
        supports_macro: 是否支持宏观指标（CPI/PMI/LPR 等）。
        markets: 支持的市场列表（如 ["cn"]、["cn", "hk", "us"]）。
    """

    supports_etf_kline: bool = False
    supports_etf_shares: bool = False
    supports_index_daily: bool = False
    supports_index_valuation: bool = False
    supports_macro: bool = False
    markets: list[str] | None = None

    def __post_init__(self) -> None:
        if self.markets is None:
            self.markets = ["cn"]

    def supports(self, capability: str) -> bool:
        """按字符串名称检查是否支持某项能力。

        Args:
            capability: 能力名称，如 "etf_kline"、"index_daily"。

        Returns:
            True 表示该适配器声明支持此能力。
        """
        mapping = {
            "etf_kline": self.supports_etf_kline,
            "etf_shares": self.supports_etf_shares,
            "index_daily": self.supports_index_daily,
            "index_valuation": self.supports_index_valuation,
            "macro": self.supports_macro,
        }
        return bool(mapping.get(capability, False))


# ---- 抽象适配器基类 ----


class BaseDataSourceAdapter(ABC):
    """数据源适配器抽象基类。

    所有数据源适配器（现有 client 包装器、未来 Tushare/YFinance 等）
    均需继承此类，实现统一接口，供 DataSourceManager 编排。

    Attributes:
        name: 数据源唯一标识（如 "akshare_fund"、"tushare"）。
        priority: 优先级（数字越小越优先，默认 100）。
    """

    name: str = "base"
    priority: int = 100

    @property
    def is_available(self) -> bool:
        """数据源是否可用（已配置且可达）。

        子类应覆写此属性，返回真实的可用性状态。
        默认返回 True。
        """
        return True

    @property
    @abstractmethod
    def capabilities(self) -> SourceCapability:
        """返回该适配器支持的能力声明。

        DataSourceManager 据此决定将请求路由到哪些适配器。

        Returns:
            SourceCapability 实例。
        """
        ...

    def health_check(self) -> HealthStatus:
        """快速连通性检测，子类应按需覆写。

        Returns:
            HealthStatus，默认返回健康（未实现真实检测）。
        """
        return HealthStatus(healthy=True, message="适配器未实现健康检查")
