"""宏观数据适配器 — 包装 AkShareMacroClient。

不修改 AkShareMacroClient 的任何代码，仅提供 BaseDataSourceAdapter 接口。
"""

from __future__ import annotations

from quant_etf_api.infra.clients.akshare_macro import AkShareMacroClient
from quant_etf_api.infra.clients.base import HealthStatus
from quant_etf_api.infra.data_sources.base import (
    BaseDataSourceAdapter,
    DataSourceUnavailableError,
    SourceCapability,
)


class MacroDataAdapter(BaseDataSourceAdapter):
    """宏观指标数据源适配器。

    包装 AkShareMacroClient，暴露统一的 BaseDataSourceAdapter 接口。

    Attributes:
        name: "akshare_macro"
        priority: 10（默认）
    """

    name = "akshare_macro"
    priority = 10

    def __init__(self, client: AkShareMacroClient | None = None) -> None:
        """初始化适配器。

        Args:
            client: AkShareMacroClient 实例，None 时自动创建。
        """
        self._client = client or AkShareMacroClient()

    @property
    def is_available(self) -> bool:
        """宏观数据源始终可用（AkShare 为免费源，无需凭证）。"""
        return True

    @property
    def capabilities(self) -> SourceCapability:
        """声明支持的宏观数据能力。"""
        return SourceCapability(
            supports_macro=True,
            markets=["cn"],
        )

    def health_check(self) -> HealthStatus:
        """委托给底层客户端的健康检查。"""
        return self._client.health_check()

    # ---- 单项指标 ----

    def fetch_cpi_monthly(self):
        """获取中国月度 CPI 同比数据。"""
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_cpi_monthly()

    def fetch_pmi(self):
        """获取中国制造业 PMI 月度数据。"""
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_pmi()

    def fetch_lpr(self):
        """获取 LPR 贷款市场报价利率。"""
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_lpr()

    def fetch_all(self):
        """一次性获取全部宏观指标（CPI + PMI + LPR），单项失败不影响其他。"""
        if not self.is_available:
            raise DataSourceUnavailableError(f"{self.name} 数据源不可用")
        return self._client.fetch_all()
