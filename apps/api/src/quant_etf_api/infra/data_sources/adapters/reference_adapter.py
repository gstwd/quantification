"""交易所参考数据适配器 — 包装 ExchangeReferenceClient。

不修改 ExchangeReferenceClient 的任何代码，仅提供 BaseDataSourceAdapter 接口。
"""

from __future__ import annotations

from typing import Any

from quant_etf_api.infra.clients.exchange_reference import ExchangeReferenceClient
from quant_etf_api.infra.clients.base import HealthStatus
from quant_etf_api.infra.data_sources.base import (
    BaseDataSourceAdapter,
    SourceCapability,
)


class ReferenceDataAdapter(BaseDataSourceAdapter):
    """交易所参考数据源适配器。

    包装 ExchangeReferenceClient（静态数据，无网络依赖）。

    Attributes:
        name: "exchange_reference"
        priority: 100（最低优先级，仅作为补充参考）
    """

    name = "exchange_reference"
    priority = 100  # 静态参考数据优先级最低

    def __init__(self, client: ExchangeReferenceClient | None = None) -> None:
        """初始化适配器。

        Args:
            client: ExchangeReferenceClient 实例，None 时自动创建。
        """
        self._client = client or ExchangeReferenceClient()

    @property
    def is_available(self) -> bool:
        """交易所参考数据为静态数据，始终可用。"""
        return True

    @property
    def capabilities(self) -> SourceCapability:
        """交易所参考数据不声明具体的量化数据能力。"""
        return SourceCapability(markets=["cn"])

    def health_check(self) -> HealthStatus:
        """静态数据无需网络检测。"""
        return self._client.health_check()

    def list_reference_sources(self) -> list[dict[str, Any]]:
        """获取参考数据源列表。

        Returns:
            数据源名称和用途列表。
        """
        return self._client.list_reference_sources()
