from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus
from quant_etf_api.infra.clients.index_daily_common import (
    IndexDailyBar,
    _build_index_bars,
    incremental_start_date,
    index_code_market_prefix,
)

# TickFlow 免费服务地址：无需注册与 API Key，仅提供历史日 K 线（1d/1w/1M/1Q/1Y）
# 与标的信息；正式服务地址用于配置了 API Key 的场景（未来如需实时/分钟级数据）
_TICKFLOW_FREE_BASE_URL = os.getenv("TICKFLOW_FREE_BASE_URL", "https://free-api.tickflow.org")
_TICKFLOW_PAID_BASE_URL = os.getenv("TICKFLOW_BASE_URL", "https://api.tickflow.org")

# 免费档按 IP 限流（默认 60 次/分钟），调用间隔（秒）可配置
_TICKFLOW_MIN_INTERVAL = float(os.getenv("TICKFLOW_INDEX_MIN_INTERVAL", "1.2"))

# 单次请求 K 线数量上限（SDK/API 硬上限 10000）与分页安全上限
_TICKFLOW_MAX_ROWS_PER_REQUEST = 10000
_TICKFLOW_MAX_PAGES = 20

_CN_TZ = ZoneInfo("Asia/Shanghai")

# 模块级节流锁：免费档限流按 IP 生效，需跨客户端实例共享时间戳
_TICKFLOW_RATE_LOCK = threading.Lock()
_TICKFLOW_LAST_CALL_TS = 0.0


def _throttle_tickflow() -> None:
    """跨实例节流 TickFlow 请求，避免触发免费档 IP 频次限制。"""
    global _TICKFLOW_LAST_CALL_TS
    with _TICKFLOW_RATE_LOCK:
        elapsed = time.monotonic() - _TICKFLOW_LAST_CALL_TS
        if elapsed < _TICKFLOW_MIN_INTERVAL:
            time.sleep(_TICKFLOW_MIN_INTERVAL - elapsed)
        _TICKFLOW_LAST_CALL_TS = time.monotonic()


class TickFlowIndexClient(BaseDataClient):
    """TickFlow 指数日线客户端（默认免费服务）。

    仅使用 TickFlow 的单标的日 K 线功能（client.klines.get，period='1d'），
    不涉及实时行情、分钟线、标的池、财务等其余方法。数据特点：
    - 免费服务无需注册与 Token（TickFlow.free 等价配置），历史日 K 免费；
      若配置了 API Key 则自动切换到正式服务（api.tickflow.org）；
    - 标的代码格式 code.SH/code.SZ，与本系统指数 sh/sz 归属规则一致，
      深证指数为 .SZ，其余（0/9/H 开头）为 .SH；
    - 实测成交量单位为手、成交额单位为元，与 index_daily_bar 表口径一致，
      与 baostock（股÷100）归一化结果逐位一致，无需换算；
    - 免费档盘中不实时更新，收盘后 1-2 小时提供当日数据，适合日频研究。

    OHLC 完整性校验由调用方（IngestService 多源切换）统一执行，
    本客户端只负责拉取与格式归一化。
    """

    source_name = "tickflow_index"

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """初始化 TickFlow 客户端。

        Args:
            api_key: TickFlow API Key；未配置时自动使用免费服务。
            timeout: 请求超时秒数。
        """
        super().__init__()
        configured_key = api_key if api_key is not None else get_settings().tickflow_api_key
        self._api_key = (configured_key or "").strip()
        self._timeout = timeout

    def is_configured(self) -> bool:
        """判断该数据源是否可用：免费档无需 Key，仅要求 SDK 已安装。"""
        try:
            import tickflow  # noqa: F401
        except Exception:
            return False
        return True

    @staticmethod
    def _symbol(index_code: str) -> str:
        """将指数代码转为 TickFlow 标的代码（code.SH / code.SZ）。

        Args:
            index_code: 指数代码，如 000300、399001、H30269。

        Returns:
            TickFlow 标的代码，如 '000300.SH'、'399001.SZ'。
        """
        market = index_code_market_prefix(index_code)
        return f"{index_code}.{market.upper()}"

    @staticmethod
    def _fmt_ms(date_str: str | None, *, end_of_day: bool = False) -> int | None:
        """将 'YYYYMMDD' 转为 TickFlow 所需的毫秒时间戳。

        Args:
            date_str: 日期字符串，如 '20260101'；None 返回 None。
            end_of_day: True 时返回当日收盘（23:59:59.999）时间戳。

        Returns:
            毫秒时间戳（Asia/Shanghai 时区）；输入为 None 时返回 None。
        """
        if date_str is None:
            return None
        dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=_CN_TZ)
        if end_of_day:
            dt = dt + timedelta(days=1) - timedelta(milliseconds=1)
        return int(dt.timestamp() * 1000)

    @staticmethod
    def _ms_to_date(ms: int) -> date:
        """将 TickFlow 毫秒时间戳转为交易日日期（Asia/Shanghai）。

        Args:
            ms: 毫秒时间戳。

        Returns:
            对应的本地日期。
        """
        return datetime.fromtimestamp(ms / 1000, _CN_TZ).date()

    def _build_client(self):
        """延迟创建 TickFlow 客户端实例（按是否配置 Key 选择免费/正式服务）。"""
        from tickflow import TickFlow

        if self._api_key:
            return TickFlow(
                api_key=self._api_key,
                base_url=_TICKFLOW_PAID_BASE_URL,
                timeout=self._timeout,
            )
        return TickFlow(
            api_key=None,
            base_url=_TICKFLOW_FREE_BASE_URL,
            timeout=self._timeout,
        )

    def _collect_rows(
        self,
        client,
        symbol: str,
        start_date: str | None,
        end_date: str | None,
    ) -> list[dict[str, Any]]:
        """按日期窗口分页拉取 TickFlow 日 K 原始行。

        单次请求最多返回 _TICKFLOW_MAX_ROWS_PER_REQUEST 根 K 线；当响应条数
        达到上限且仍早于请求起点时向前翻页，直到取完或到达安全页数上限。

        Args:
            client: TickFlow 客户端实例。
            symbol: TickFlow 标的代码。
            start_date: 起始日 'YYYYMMDD'，None 表示最早。
            end_date: 结束日 'YYYYMMDD'，None 表示最新。

        Returns:
            原始行字典列表（含 trade_date/open/high/low/close/volume/amount）。
        """
        requested_start_ms = self._fmt_ms(start_date)
        page_end_ms = self._fmt_ms(end_date, end_of_day=True)
        rows: list[dict[str, Any]] = []
        for _ in range(_TICKFLOW_MAX_PAGES):
            _throttle_tickflow()
            raw = client.klines.get(
                symbol,
                period="1d",
                count=_TICKFLOW_MAX_ROWS_PER_REQUEST,
                start_time=requested_start_ms,
                end_time=page_end_ms,
                adjust="none",
            )
            if not isinstance(raw, dict):
                break
            timestamps = raw.get("timestamp") or []
            if not timestamps:
                break
            page_rows = len(timestamps)
            amounts = raw.get("amount") or [0.0] * page_rows
            for i in range(page_rows):
                rows.append(
                    {
                        "trade_date": self._ms_to_date(int(timestamps[i])).isoformat(),
                        "open": raw["open"][i],
                        "high": raw["high"][i],
                        "low": raw["low"][i],
                        "close": raw["close"][i],
                        "volume": raw["volume"][i],
                        "amount": amounts[i],
                    }
                )
            first_ts = int(timestamps[0])
            # 响应未满一页说明已无更早数据；已覆盖请求起点也可停止
            if page_rows < _TICKFLOW_MAX_ROWS_PER_REQUEST:
                break
            if requested_start_ms is not None and first_ts <= requested_start_ms:
                break
            next_page_end = first_ts - 1
            if page_end_ms is not None and next_page_end >= page_end_ms:
                break
            page_end_ms = next_page_end
        else:
            self._logger.warning(
                "指数 %s 日 K 历史超过 %d 页仍未取完，可能被截断",
                symbol,
                _TICKFLOW_MAX_PAGES,
            )
        return rows

    def _fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过 TickFlow 拉取指数日线（klines.get，period='1d'）。

        Args:
            index_code: 指数代码，如 000300。
            start_date: 起始日 'YYYYMMDD'，None 表示最早。
            end_date: 结束日 'YYYYMMDD'，None 表示最新。

        Returns:
            按日期升序排列的日线数据列表。
        """
        endpoint = "klines.get"
        symbol = self._symbol(index_code)
        self._log_request(endpoint, {"index_code": index_code, "symbol": symbol})
        start = time.perf_counter()
        try:
            client = self._build_client()
            try:
                rows = self._collect_rows(client, symbol, start_date, end_date)
            finally:
                client.close()
            if not rows:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return []

            df = pd.DataFrame(rows)
            df = (
                df.drop_duplicates(subset=["trade_date"])
                .sort_values("trade_date")
                .reset_index(drop=True)
            )
            bars = _build_index_bars(
                df,
                date_col="trade_date",
                open_col="open",
                close_col="close",
                high_col="high",
                low_col="low",
                volume_col="volume",
                amount_col="amount",
                start_date=start_date,
                end_date=end_date,
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(bars), elapsed)
            return bars
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
        """拉取指数日线 OHLCV 数据（tickflow 单源入口）。

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
        start = incremental_start_date(since_date)
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
                message="tickflow 指数接口可达" if ok else "tickflow 指数接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
