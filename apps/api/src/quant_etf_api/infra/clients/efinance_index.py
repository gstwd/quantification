from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import date, datetime, timedelta

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus
from quant_etf_api.infra.clients.index_daily_common import (
    IndexDailyBar,
    _build_index_bars,
    _incremental_start_date,
    index_code_market_prefix,
)
from quant_etf_api.infra.clients.retry import with_retry

# efinance 库内部使用 requests 无超时，东财主机不可达时可能长时间阻塞；
# 统一在独立线程中执行并限制等待时间（秒），超时后仅放弃等待，线程由进程回收
_EFINANCE_CALL_TIMEOUT = float(os.getenv("EFINANCE_INDEX_TIMEOUT", "15"))

# 模块级共享线程池：超时保护的 efinance 调用复用线程，避免每次调用新建线程池
_EF_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="efinance-index")


class EfinanceIndexClient(BaseDataClient):
    """东方财富指数日线客户端（基于 efinance SDK）。

    仅使用 efinance 的指数日 K 线功能（ef.stock.get_quote_history + quote_id_mode
    直传东方财富 secid），不涉及个股、基金、期货等其余方法。数据来自东方财富行情接口：
    - 覆盖所有常见 A 股指数（上证/深证/中证系），免费且无需 Token；
    - 成交量单位为手、成交额单位为元，与 index_daily_bar 表口径一致，无需换算；
    - 支持服务端日期过滤（beg/end 参数，格式 YYYYMMDD）。
    - 直传 secid（如 1.000300）绕过代码搜索，避免 sh/sz 前缀不被识别的兼容问题。

    OHLC 完整性校验由调用方（AkShareIndexClient 同级降级链或 IngestService
    多源切换）统一执行，本客户端只负责拉取与格式归一化。
    """

    source_name = "efinance_index"

    @staticmethod
    def _call_with_timeout(fn, timeout: float = _EFINANCE_CALL_TIMEOUT):
        """在独立线程中执行 efinance 调用，超时则抛出 TimeoutError。

        efinance 内部请求未设置超时，此包装保证调用方在限定时间内返回；
        超时后取消 future 并抛错（线程无法强制终止，但不再阻塞调用方）。

        Args:
            fn: 无参可调用对象。
            timeout: 超时秒数。

        Returns:
            函数返回值。

        Raises:
            TimeoutError: 执行超时。
        """
        future = _EF_EXECUTOR.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            future.cancel()
            raise

    @staticmethod
    def _fmt_date(value: str | None) -> str | None:
        """将 'YYYYMMDD' 转为 efinance 所需的 'YYYY-MM-DD' 参数格式。

        Args:
            value: 日期字符串，如 '20260101'。

        Returns:
            'YYYY-MM-DD' 格式字符串；None 原样返回。
        """
        if value is None:
            return None
        return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")

    @staticmethod
    def _to_secid(index_code: str) -> str:
        """将指数代码转为东方财富 secid（1=沪市指数，0=深市指数）。

        Args:
            index_code: 指数代码，如 000300。

        Returns:
            东方财富 secid，如 '1.000300'。
        """
        market = 1 if index_code_market_prefix(index_code) == "sh" else 0
        return f"{market}.{index_code}"

    # 仅重试瞬时网络错误；超时异常不重试（超时已消耗等待时间，重试会让失效源拖慢降级链）
    @with_retry(retryable=(ConnectionError, ConnectionResetError, OSError, AttributeError))
    def _fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过 efinance 拉取指数日线（东方财富源）。

        Args:
            index_code: 指数代码，如 000300。
            start_date: 起始日 'YYYYMMDD'，None 表示最早。
            end_date: 结束日 'YYYYMMDD'，None 表示最新。

        Returns:
            按日期升序排列的日线数据列表。
        """
        endpoint = "get_quote_history"
        secid = self._to_secid(index_code)
        self._log_request(endpoint, {"index_code": index_code, "secid": secid})
        start = time.perf_counter()
        try:
            import efinance as ef

            df = self._call_with_timeout(
                lambda: ef.stock.get_quote_history(
                    secid,
                    beg=self._fmt_date(start_date) or "19000101",
                    end=self._fmt_date(end_date) or date.today().strftime("%Y-%m-%d"),
                    quote_id_mode=True,
                )
            )
            bars = _build_index_bars(
                df,
                date_col="日期",
                open_col="开盘",
                close_col="收盘",
                high_col="最高",
                low_col="最低",
                volume_col="成交量",
                amount_col="成交额",
                start_date=start_date,
                end_date=end_date,
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(bars), elapsed)
            return bars
        except FutureTimeoutError:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(
                endpoint,
                TimeoutError(f"efinance 调用超时（{_EFINANCE_CALL_TIMEOUT:.0f}s）"),
                elapsed,
            )
            raise
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """拉取指数日线 OHLCV 数据（efinance 单源入口）。

        Args:
            index_code: 指数代码，如 000300。
            start_date: 起始日 'YYYYMMDD'，None 表示最早。
            end_date: 结束日 'YYYYMMDD'，None 表示最新。

        Returns:
            按日期升序排列的日线数据列表。
        """
        return self._fetch_index_daily(index_code, start_date, end_date)

    def fetch_index_daily_since(self, index_code: str, since_date: date) -> list[IndexDailyBar]:
        """增量拉取指数日线：仅拉取 since_date 之前的缓冲窗口到最新。

        Args:
            index_code: 指数代码，如 000300。
            since_date: 起始日期（不含，即仅拉取该日之后的数据）。

        Returns:
            按日期升序排列的日线数据列表（含缓冲窗口内的历史行）。
        """
        start = _incremental_start_date(since_date)
        return self.fetch_index_daily(index_code, start_date=start.strftime("%Y%m%d"))

    def health_check(self) -> HealthStatus:
        """通过拉取上证指数最近一周数据检测连通性。"""
        try:
            start = time.perf_counter()
            start_date = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
            bars = self.fetch_index_daily("000001", start_date=start_date)
            elapsed = (time.perf_counter() - start) * 1000
            ok = len(bars) > 0
            return HealthStatus(
                healthy=ok,
                message="efinance 指数接口可达" if ok else "efinance 指数接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
