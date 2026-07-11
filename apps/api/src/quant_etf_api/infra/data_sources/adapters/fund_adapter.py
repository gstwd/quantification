"""ETF 基金数据适配器 — 包装 AkShareFundClient。

不修改 AkShareFundClient 的任何代码，仅提供 BaseDataSourceAdapter 接口。
"""

from __future__ import annotations

from quant_etf_api.infra.clients.akshare_fund import AkShareFundClient
from quant_etf_api.infra.clients.base import HealthStatus
from quant_etf_api.infra.data_sources.base import (
    BaseDataSourceAdapter,
    DataSourceUnavailableError,
    SourceCapability,
)


class FundDataAdapter(BaseDataSourceAdapter):
    """ETF 基金数据源适配器。

    包装 AkShareFundClient，暴露统一的 BaseDataSourceAdapter 接口，
    供 DataSourceManager 按优先级编排。

    Attributes:
        name: "akshare_fund"
        priority: 10（默认，可通过 settings.data_source_priority 调整）
    """

    name = "akshare_fund"
    priority = 10  # 默认优先级，DataSourceManager 可根据配置动态调整

    def __init__(self, client: AkShareFundClient | None = None) -> None:
        """初始化适配器。

        Args:
            client: AkShareFundClient 实例，None 时自动创建。
        """
        self._client = client or AkShareFundClient()

    @property
    def is_available(self) -> bool:
        """ETF 基金数据源始终可用（AkShare 为免费源，无需凭证）。"""
        return True

    @property
    def capabilities(self) -> SourceCapability:
        """声明支持的 ETF 数据能力。"""
        return SourceCapability(
            supports_etf_kline=True,
            supports_etf_shares=True,
            markets=["cn"],
        )

    def health_check(self) -> HealthStatus:
        """委托给底层客户端的健康检查。"""
        return self._client.health_check()

    # ---- ETF 信息 ----

    def fetch_etf_info(self, code: str):
        """获取 ETF 基本信息。

        Args:
            code: ETF 代码（如 "510050"）。

        Returns:
            AkShareEtfInfo 或 None。
        """
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_etf_info(code)

    # ---- ETF 日线行情 ----

    def fetch_etf_daily_bars(
        self,
        code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """获取 ETF 日线行情（OHLCV）。

        Args:
            code: ETF 代码（如 "510050"）。
            start_date: 起始日期 "YYYYMMDD"，None 表示 90 天前。
            end_date: 结束日期 "YYYYMMDD"，None 表示今天。

        Returns:
            AkShareEtfDailyBar 列表。
        """
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_etf_daily_bars(code, start_date, end_date)

    # ---- ETF 份额快照 ----

    def fetch_share_snapshot(self, code: str):
        """获取 ETF 份额/规模快照。

        Args:
            code: ETF 代码（如 "510050"）。

        Returns:
            AkShareEtfShareSnapshot 或 None。
        """
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_share_snapshot(code)
