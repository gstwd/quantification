from __future__ import annotations

from typing import Any

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus


class ExchangeReferenceClient(BaseDataClient):
    """交易所参考数据客户端。

    提供交易所基本信息列表，后续可扩展为从交易所官网拉取 ETF 列表等数据。
    """

    source_name = "exchange_reference"

    def list_reference_sources(self) -> list[dict[str, Any]]:
        """获取参考数据源列表。"""
        self._log_request("list_reference_sources")
        sources = [
            {"name": "sse", "purpose": "ETF 主数据与份额补充"},
            {"name": "szse", "purpose": "ETF 主数据与公告补充"},
        ]
        self._log_response("list_reference_sources", len(sources), 0)
        return sources

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, message="交易所参考数据为静态数据，无需网络检测")
