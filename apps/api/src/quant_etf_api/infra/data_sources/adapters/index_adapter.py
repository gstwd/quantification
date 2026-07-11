"""指数行情适配器 — 包装 AkShareIndexClient。

不修改 AkShareIndexClient 的任何代码，仅提供 BaseDataSourceAdapter 接口。
"""

from __future__ import annotations

from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.clients.base import HealthStatus
from quant_etf_api.infra.data_sources.base import (
    BaseDataSourceAdapter,
    DataSourceUnavailableError,
    SourceCapability,
)


class IndexDataAdapter(BaseDataSourceAdapter):
    """指数行情数据源适配器。

    包装 AkShareIndexClient（本身已内置腾讯→中证→东方财富三层降级），
    暴露统一的 BaseDataSourceAdapter 接口。

    Attributes:
        name: "akshare_index"
        priority: 10（默认，可通过 settings.data_source_priority 调整）
    """

    name = "akshare_index"
    priority = 10

    def __init__(self, client: AkShareIndexClient | None = None) -> None:
        """初始化适配器。

        Args:
            client: AkShareIndexClient 实例，None 时自动创建。
        """
        self._client = client or AkShareIndexClient()

    @property
    def is_available(self) -> bool:
        """指数数据源始终可用（AkShare 为免费源，无需凭证）。"""
        return True

    @property
    def capabilities(self) -> SourceCapability:
        """声明支持的指数数据能力。"""
        return SourceCapability(
            supports_index_daily=True,
            supports_index_valuation=True,
            markets=["cn"],
        )

    def health_check(self) -> HealthStatus:
        """委托给底层客户端的健康检查。"""
        return self._client.health_check()

    # ---- 指数名称 ----

    def fetch_index_name(self, index_code: str) -> str | None:
        """根据指数代码查询中文名称（内置三级降级）。

        Args:
            index_code: 指数代码（如 "000300"）。

        Returns:
            指数中文名称或 None。
        """
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_index_name(index_code)

    def find_index_code_by_name(self, name: str) -> str | None:
        """根据指数名称反查代码。

        Args:
            name: 指数名称（如 "沪深300指数"）。

        Returns:
            指数代码或 None。
        """
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.find_index_code_by_name(name)

    # ---- 指数日线 ----

    def fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """获取指数日线行情（内置腾讯→中证→东方财富三级降级）。

        Args:
            index_code: 指数代码（如 "000300"）。
            start_date: 起始日 "YYYYMMDD"。
            end_date: 结束日 "YYYYMMDD"。

        Returns:
            IndexDailyBar 列表。
        """
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_index_daily(index_code, start_date, end_date)

    # ---- 指数估值 ----

    def fetch_index_valuation(self, index_code: str):
        """获取指数估值 PE/PB 及历史分位（内置乐股乐源→中证官网双层降级）。

        Args:
            index_code: 指数代码（如 "000300"）。

        Returns:
            IndexValuation 列表。
        """
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_index_valuation(index_code)
