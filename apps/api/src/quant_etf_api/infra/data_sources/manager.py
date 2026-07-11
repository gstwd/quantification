"""数据源管理器 — 多数据源的优先级编排和故障切换。

参考 DSA 项目的 DataFetcherManager 模式，结合本项目以指数/ETF 数据
为核心的特点简化设计。

核心职责：
1. 管理多个数据源适配器（按优先级排序）
2. 基于 CircuitBreaker 的自动故障切换
3. 按能力（capability）路由请求到支持该能力的数据源
4. 提供统一的数据获取接口
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from quant_etf_api.infra.data_sources.base import (
    BaseDataSourceAdapter,
    DataSourceError,
    SourceCapability,
)
from quant_etf_api.infra.data_sources.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class DataSourceManager:
    """数据源管理器，负责多数据源的优先级编排和故障切换。

    使用示例::

        manager = DataSourceManager()
        manager.register(FundDataAdapter())
        manager.register(IndexDataAdapter())
        bars = manager.fetch_etf_daily_bar("510050", "2024-01-01", "2024-12-31")

    也可以通过配置覆盖默认优先级::

        manager = DataSourceManager.from_settings(settings)
    """

    def __init__(
        self,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        """初始化管理器。

        Args:
            circuit_breaker: 熔断器实例，None 时使用默认参数创建。
        """
        self._adapters: list[BaseDataSourceAdapter] = []
        self._breaker = circuit_breaker or CircuitBreaker()

    def register(self, adapter: BaseDataSourceAdapter) -> None:
        """注册一个数据源适配器，按优先级排序插入。

        Args:
            adapter: BaseDataSourceAdapter 实例。
        """
        self._adapters.append(adapter)
        self._adapters.sort(key=lambda a: a.priority)
        logger.debug(
            "数据源已注册: name=%s priority=%d markets=%s",
            adapter.name,
            adapter.priority,
            adapter.capabilities.markets,
        )

    # ---- 统一数据获取接口 ----

    def fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """获取指数日线行情，自动在多数据源间故障切换。

        Args:
            index_code: 指数代码（如 "000300"）。
            start_date: 起始日 "YYYYMMDD"。
            end_date: 结束日 "YYYYMMDD"。

        Returns:
            (数据列表, 实际使用的数据源名称)

        Raises:
            DataSourceError: 所有数据源均失败。
        """
        adapters = self._find_adapters_for("index_daily", "cn")

        def _fetch(adapter: BaseDataSourceAdapter) -> list[dict[str, Any]]:
            result = adapter.fetch_index_daily(index_code, start_date, end_date)
            if result is None:
                return []
            # 如果有 __dict__ 属性（dataclass），转为 dict 列表
            if hasattr(result, "__iter__") and result:
                first = next(iter(result)) if hasattr(result, "__iter__") else None
                if first and hasattr(first, "__dict__"):
                    # dataclass 列表
                    converted: list[dict[str, Any]] = []
                    for item in result:
                        converted.append({
                            k: v for k, v in item.__dict__.items()
                        })
                    return converted
            if isinstance(result, list):
                return result
            return list(result) if result else []

        result, source = self._try_fetch(adapters, _fetch, "index_daily", "cn")
        return result or [], source or "unknown"

    def fetch_index_valuation(
        self,
        index_code: str,
    ) -> tuple[list[dict[str, Any]], str]:
        """获取指数估值 PE/PB，自动故障切换。

        Args:
            index_code: 指数代码（如 "000300"）。

        Returns:
            (估值数据列表, 实际使用的数据源名称)
        """
        adapters = self._find_adapters_for("index_valuation", "cn")

        def _fetch(adapter: BaseDataSourceAdapter) -> list[dict[str, Any]]:
            result = adapter.fetch_index_valuation(index_code)
            if result is None:
                return []
            if isinstance(result, list):
                return result
            return list(result)

        result, source = self._try_fetch(adapters, _fetch, "index_valuation", "cn")
        return result or [], source or "unknown"

    def fetch_etf_daily_bar(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """获取 ETF 日线行情，自动故障切换。

        Args:
            symbol: ETF 代码（如 "510050"）。
            start_date: 起始日 "YYYYMMDD"。
            end_date: 结束日 "YYYYMMDD"。

        Returns:
            (日线数据列表, 实际使用的数据源名称)
        """
        adapters = self._find_adapters_for("etf_kline", "cn")

        def _fetch(adapter: BaseDataSourceAdapter) -> list[dict[str, Any]]:
            result = adapter.fetch_etf_daily_bars(symbol, start_date, end_date)
            if result is None:
                return []
            if isinstance(result, list):
                return result
            return list(result)

        result, source = self._try_fetch(adapters, _fetch, "etf_kline", "cn")
        return result or [], source or "unknown"

    def fetch_etf_shares(
        self,
        symbol: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """获取 ETF 份额快照，自动故障切换。

        Args:
            symbol: ETF 代码（如 "510050"）。

        Returns:
            (份额数据字典或 None, 数据源名称)
        """
        adapters = self._find_adapters_for("etf_shares", "cn")

        def _fetch(adapter: BaseDataSourceAdapter) -> dict[str, Any] | None:
            result = adapter.fetch_share_snapshot(symbol)
            if result is not None and hasattr(result, "__dict__"):
                return {k: v for k, v in result.__dict__.items()}
            return result

        result, source = self._try_fetch(adapters, _fetch, "etf_shares", "cn")
        return result, source or "unknown"

    def fetch_macro_indicators(
        self,
    ) -> tuple[list[dict[str, Any]], str]:
        """获取全部宏观指标（CPI + PMI + LPR），自动故障切换。

        Returns:
            (MacroIndicator 列表, 数据源名称)
        """
        adapters = self._find_adapters_for("macro", "cn")

        def _fetch(adapter: BaseDataSourceAdapter) -> list[dict[str, Any]]:
            result = adapter.fetch_all()
            if result is None:
                return []
            if isinstance(result, list):
                return result
            return list(result)

        result, source = self._try_fetch(adapters, _fetch, "macro", "cn")
        return result or [], source or "unknown"

    # ---- 属性 ----

    @property
    def available_sources(self) -> list[str]:
        """返回所有已注册且可用的数据源名称列表。"""
        return [
            a.name for a in self._adapters if a.is_available
        ]

    def get_source_status(self) -> dict[str, Any]:
        """获取所有数据源的状态概要（用于诊断）。

        Returns:
            {source_name: {"available": bool, "priority": int, "capabilities": ...}}
        """
        result: dict[str, Any] = {}
        for a in self._adapters:
            cb_status = self._breaker.get_status(
                f"data_source:{a.name}"
            )
            result[a.name] = {
                "available": a.is_available,
                "priority": a.priority,
                "circuit": cb_status["state"],
                "failures": cb_status["failures"],
                "capabilities": {
                    "etf_kline": a.capabilities.supports_etf_kline,
                    "index_daily": a.capabilities.supports_index_daily,
                    "index_valuation": a.capabilities.supports_index_valuation,
                    "macro": a.capabilities.supports_macro,
                },
                "markets": a.capabilities.markets,
            }
        return result

    # ---- 内部方法 ----

    def _find_adapters_for(
        self,
        capability: str,
        market: str = "cn",
    ) -> list[BaseDataSourceAdapter]:
        """查找支持指定能力和市场的适配器列表（按优先级排序）。

        排除：
        - 不支持该 capability 的适配器
        - 不覆盖目标市场的适配器
        - is_available 为 False 的适配器

        Args:
            capability: 能力名称（如 "index_daily"）。
            market: 目标市场（默认 "cn"）。

        Returns:
            符合条件的适配器列表（已按优先级排序）。
        """
        result: list[BaseDataSourceAdapter] = []
        for adapter in self._adapters:
            # 检查能力
            if not adapter.capabilities.supports(capability):
                continue
            # 检查市场
            markets = adapter.capabilities.markets or []
            if market not in markets:
                continue
            # 检查可用性
            if not adapter.is_available:
                logger.debug(
                    "数据源 %s 不可用，跳过 capability=%s",
                    adapter.name,
                    capability,
                )
                continue
            result.append(adapter)
        return result

    def _try_fetch(
        self,
        adapters: list[BaseDataSourceAdapter],
        fetcher: Callable[[BaseDataSourceAdapter], Any],
        capability: str,
        market: str,
    ) -> tuple[Any, str | None]:
        """按优先级依次尝试从适配器获取数据。

        内置熔断检查：被熔断的适配器自动跳过。
        成功时记录 success，失败时记录 failure（可能触发熔断）。

        Args:
            adapters: 已排序的适配器列表。
            fetcher: 接受 adapter 返回数据的回调函数。
            capability: 能力标识（用于熔断 key）。
            market: 市场标识（用于熔断 key）。

        Returns:
            (数据, 数据源名称)

        Raises:
            DataSourceError: 所有适配器均失败。
        """
        errors: list[str] = []

        for adapter in adapters:
            cb_key = f"{capability}:{market}:{adapter.name}"

            # 熔断检查
            if not self._breaker.is_available(cb_key):
                logger.debug(
                    "数据源 %s 已熔断，跳过 capability=%s",
                    adapter.name,
                    capability,
                )
                errors.append(
                    f"{adapter.name}: 已熔断（冷却中）"
                )
                continue

            try:
                result = fetcher(adapter)
                # 成功
                self._breaker.record_success(cb_key)
                logger.info(
                    "数据获取成功: source=%s capability=%s market=%s",
                    adapter.name,
                    capability,
                    market,
                )
                return result, adapter.name
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                errors.append(f"{adapter.name}: {error_msg}")
                self._breaker.record_failure(cb_key, error_msg)
                logger.warning(
                    "数据源 %s 获取失败 capability=%s: %s",
                    adapter.name,
                    capability,
                    exc,
                )

        # 全部失败
        aggregated = "; ".join(errors)
        raise DataSourceError(
            f"所有数据源均无法获取 {capability}:{market} 数据。详情: {aggregated}"
        )

    @classmethod
    def from_settings(cls, settings: Any = None) -> DataSourceManager:
        """从项目 Settings 构建预配置的 DataSourceManager。

        自动注册所有内置适配器和已配置的占位符。

        Args:
            settings: Settings 实例，None 时自动获取。

        Returns:
            已注册全部适配器的 DataSourceManager 实例。
        """
        if settings is None:
            try:
                from quant_etf_api.config.settings import get_settings
                settings = get_settings()
            except Exception:
                settings = None

        manager = cls(
            circuit_breaker=CircuitBreaker(
                failure_threshold=getattr(
                    settings, "circuit_breaker_failure_threshold", 3
                ),
                cooldown_seconds=getattr(
                    settings, "circuit_breaker_cooldown_seconds", 300.0
                ),
            ),
        )

        # 注册现有数据源适配器
        from quant_etf_api.infra.data_sources.adapters.fund_adapter import (
            FundDataAdapter,
        )
        from quant_etf_api.infra.data_sources.adapters.index_adapter import (
            IndexDataAdapter,
        )
        from quant_etf_api.infra.data_sources.adapters.macro_adapter import (
            MacroDataAdapter,
        )
        from quant_etf_api.infra.data_sources.adapters.reference_adapter import (
            ReferenceDataAdapter,
        )

        manager.register(FundDataAdapter())
        manager.register(IndexDataAdapter())
        manager.register(MacroDataAdapter())
        manager.register(ReferenceDataAdapter())

        # 注册占位符（仅在配置了对应凭证时激活）
        from quant_etf_api.infra.data_sources.placeholders.tushare import (
            TusharePlaceholder,
        )
        from quant_etf_api.infra.data_sources.placeholders.yfinance import (
            YFinancePlaceholder,
        )

        tushare = TusharePlaceholder()
        if tushare.is_available:
            manager.register(tushare)
            logger.info("Tushare 占位符已注册（待实现）")

        yfinance = YFinancePlaceholder()
        if yfinance.is_available:
            manager.register(yfinance)
            logger.info("YFinance 占位符已注册（待实现）")

        # 按 settings 中的 data_source_priority 调整优先级
        priority_str = getattr(settings, "data_source_priority", "")
        if priority_str:
            _apply_priority_override(manager, priority_str)

        return manager


def _apply_priority_override(
    manager: DataSourceManager,
    priority_str: str,
) -> None:
    """根据配置的优先级字符串调整适配器优先级。

    Args:
        manager: DataSourceManager 实例。
        priority_str: 逗号分隔的数据源名称（如 "tushare,akshare_index,akshare_fund"）。
    """
    names = [n.strip() for n in priority_str.split(",") if n.strip()]
    for i, name in enumerate(names):
        for adapter in manager._adapters:
            if adapter.name == name:
                adapter.priority = i
                logger.debug(
                    "数据源优先级调整: %s -> %d", adapter.name, i
                )
                break
    # 重新排序
    manager._adapters.sort(key=lambda a: a.priority)
