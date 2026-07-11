"""YFinance 数据源占位适配器。

为未来支持美股/港股指数和 ETF 数据预留接口。
当前不实现实际数据获取，仅为架构完整性和未来扩展预留。
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

_NOT_IMPLEMENTED_MSG = (
    "YFinance 数据源适配器尚未实现，当前为占位适配器。如需使用请完成适配器开发。"
)


class YFinancePlaceholder(BaseDataSourceAdapter):
    """YFinance 数据源占位适配器。

    为美股/港股/日股/韩股/台股指数和 ETF 数据预留。
    当 YFINANCE_ENABLED 环境变量设置为 "true" 时 is_available 返回 True，
    但所有数据获取方法均抛出 DataSourceUnavailableError。

    Attributes:
        name: "yfinance"
        priority: 20（海外数据源，优先级低于国内付费源）
    """

    name = "yfinance"
    priority = 20  # 海外源优先级低于国内源

    def __init__(self) -> None:
        """初始化占位适配器，检测 YFINANCE_ENABLED 环境变量。"""
        self._enabled = os.getenv("YFINANCE_ENABLED", "").strip().lower() == "true"

    @property
    def is_available(self) -> bool:
        """仅当 YFINANCE_ENABLED=true 时标记为可用。"""
        if self._enabled:
            logger.debug(
                "YFinance 占位适配器已激活（YFINANCE_ENABLED=true），但尚未实现数据获取"
            )
        return self._enabled

    @property
    def capabilities(self) -> SourceCapability:
        """声明支持海外市场全能力。"""
        return SourceCapability(
            supports_etf_kline=True,
            supports_index_daily=True,
            supports_index_valuation=True,
            markets=["cn", "hk", "us", "jp", "kr", "tw"],
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
