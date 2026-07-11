"""Tushare 数据源占位适配器。

当用户配置了 TUSHARE_TOKEN 环境变量时激活（is_available=True），
但当前不实现实际数据获取，仅为未来扩展预留接口和优先级位置。
"""

from __future__ import annotations

import logging
import os

from quant_etf_api.infra.clients.base import HealthStatus
from quant_etf_api.infra.data_sources.base import (
    BaseDataSourceAdapter,
    DataSourceUnavailableError,
    SourceCapability,
)

logger = logging.getLogger(__name__)

_NOT_IMPLEMENTED_MSG = "Tushare 数据源适配器尚未实现，当前为占位适配器。如需使用请完成适配器开发。"


class TusharePlaceholder(BaseDataSourceAdapter):
    """Tushare Pro 数据源占位适配器。

    当环境变量 TUSHARE_TOKEN 存在时 is_available 返回 True，
    但所有数据获取方法均抛出 DataSourceUnavailableError，
    提示该数据源尚未完成适配。

    Attributes:
        name: "tushare"
        priority: 5（比 AkShare 系列更高，配置后优先使用）
    """

    name = "tushare"
    priority = 5  # 高于 akshare 系列（10），配置后优先尝试

    def __init__(self) -> None:
        """初始化占位适配器，检测 TUSHARE_TOKEN 环境变量。"""
        self._token = (os.getenv("TUSHARE_TOKEN") or "").strip()

    @property
    def is_available(self) -> bool:
        """仅当配置了 TUSHARE_TOKEN 时标记为可用。"""
        available = bool(self._token)
        if available:
            logger.debug("Tushare 占位适配器已激活（TUSHARE_TOKEN 已配置），但尚未实现数据获取")
        return available

    @property
    def capabilities(self) -> SourceCapability:
        """声明支持的 A 股全能力（预期最终支持所有数据类型）。"""
        return SourceCapability(
            supports_etf_kline=True,
            supports_etf_shares=True,
            supports_index_daily=True,
            supports_index_valuation=True,
            supports_macro=True,
            markets=["cn"],
        )

    def health_check(self) -> HealthStatus:
        """占位适配器不执行真实健康检查。"""
        return HealthStatus(
            healthy=False,
            message=_NOT_IMPLEMENTED_MSG,
        )

    # ---- 所有 fetch 方法统一抛出 DataSourceUnavailableError ----

    def _raise_not_implemented(self) -> None:
        raise DataSourceUnavailableError(_NOT_IMPLEMENTED_MSG)

    def fetch_index_daily(self, *args, **kwargs):  # noqa: ANN
        self._raise_not_implemented()

    def fetch_index_valuation(self, *args, **kwargs):  # noqa: ANN
        self._raise_not_implemented()

    def fetch_etf_daily_bars(self, *args, **kwargs):  # noqa: ANN
        self._raise_not_implemented()

    def fetch_etf_info(self, *args, **kwargs):  # noqa: ANN
        self._raise_not_implemented()

    def fetch_share_snapshot(self, *args, **kwargs):  # noqa: ANN
        self._raise_not_implemented()

    def fetch_cpi_monthly(self, *args, **kwargs):  # noqa: ANN
        self._raise_not_implemented()

    def fetch_pmi(self, *args, **kwargs):  # noqa: ANN
        self._raise_not_implemented()

    def fetch_lpr(self, *args, **kwargs):  # noqa: ANN
        self._raise_not_implemented()

    def fetch_all(self, *args, **kwargs):  # noqa: ANN
        self._raise_not_implemented()
