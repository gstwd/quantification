from __future__ import annotations

import os
import time
from datetime import date, timedelta

import pandas as pd

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus
from quant_etf_api.infra.clients.index_daily_common import (
    IndexDailyBar,
    _build_index_bars,
    _incremental_start_date,
    index_code_market_prefix,
)
from quant_etf_api.infra.clients.retry import with_retry

# Tushare 免费档对指数日线接口有每分钟调用频次限制，调用间隔（秒）可配置
_TUSHARE_MIN_INTERVAL = float(os.getenv("TUSHARE_INDEX_MIN_INTERVAL", "1.0"))

# 全量历史默认起点：Tushare 指数日线数据自 1991 年起
_TUSHARE_HISTORY_START = "19910101"


class TushareIndexClient(BaseDataClient):
    """Tushare Pro 指数日线客户端（基于 tushare SDK）。

    仅使用 Tushare Pro 的指数日线功能（pro.index_daily），不涉及股票、基金、
    财务等其余接口。数据来自 Tushare Pro：
    - 需配置 TUSHARE_TOKEN（QUANT_ETF_TUSHARE_TOKEN），未配置时该源不可用；
    - ts_code 格式为 代码 + 交易所后缀：9/H 开头中证指数用 .CSI（如 H30269.CSI），
      其余沪市指数用 .SH（如 000001.SH）、深市指数用 .SZ（如 399001.SZ）；
    - 成交量单位手、成交额单位千元，本客户端将成交额 ×1000 归一化为元，
      与 index_daily_bar 表口径保持一致；
    - 免费档有频次限制，调用间按 _TUSHARE_MIN_INTERVAL 节流。
    """

    source_name = "tushare_index"

    def __init__(self, token: str | None = None) -> None:
        """初始化 Tushare 客户端。

        Args:
            token: Tushare Pro Token；未传入时读取 QUANT_ETF_TUSHARE_TOKEN。
        """
        super().__init__()
        self._token = token or get_settings().tushare_token
        self._pro = None
        self._last_call_ts = 0.0

    def is_configured(self) -> bool:
        """判断是否已配置 Tushare Token（未配置时该数据源不可用）。"""
        return bool(self._token)

    def _get_pro(self):
        """延迟创建 Tushare Pro API 实例（仅首次调用时初始化）。"""
        if self._pro is None:
            if not self._token:
                raise RuntimeError("未配置 TUSHARE_TOKEN，tushare 数据源不可用")
            import tushare as ts

            ts.set_token(self._token)
            self._pro = ts.pro_api()
        return self._pro

    def _throttle(self) -> None:
        """按最小调用间隔节流，避免触发 Tushare 频次限制。"""
        elapsed = time.monotonic() - self._last_call_ts
        if elapsed < _TUSHARE_MIN_INTERVAL:
            time.sleep(_TUSHARE_MIN_INTERVAL - elapsed)
        self._last_call_ts = time.monotonic()

    @with_retry()
    def _fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过 Tushare Pro 拉取指数日线。

        Args:
            index_code: 指数代码，如 000300。
            start_date: 起始日 'YYYYMMDD'，None 表示 19910101。
            end_date: 结束日 'YYYYMMDD'，None 表示今天。

        Returns:
            按日期升序排列的日线数据列表。
        """
        endpoint = "index_daily"
        if index_code.startswith(("9", "H", "h")):
            # 中证指数公司发布的策略/行业/主题指数（930/931/932/H 系列）使用 .CSI 后缀
            suffix = ".CSI"
        elif index_code_market_prefix(index_code) == "sh":
            suffix = ".SH"
        else:
            suffix = ".SZ"
        ts_code = f"{index_code}{suffix}"
        self._log_request(endpoint, {"index_code": index_code, "ts_code": ts_code})
        start = time.perf_counter()
        try:
            pro = self._get_pro()
            self._throttle()
            df = pro.index_daily(
                ts_code=ts_code,
                start_date=start_date or _TUSHARE_HISTORY_START,
                end_date=end_date or date.today().strftime("%Y%m%d"),
            )
            if df is None or df.empty:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return []
            # 归一化单位：成交额千元 → 元
            df = df.copy()
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0) * 1000.0
            bars = _build_index_bars(
                df,
                date_col="trade_date",
                open_col="open",
                close_col="close",
                high_col="high",
                low_col="low",
                volume_col="vol",
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
        """拉取指数日线 OHLCV 数据（tushare 单源入口）。

        Args:
            index_code: 指数代码，如 000300。
            start_date: 起始日 'YYYYMMDD'，None 表示最早。
            end_date: 结束日 'YYYYMMDD'，None 表示最新。

        Returns:
            按日期升序排列的日线数据列表。
        """
        if not self.is_configured():
            self._logger.warning("未配置 TUSHARE_TOKEN，跳过 tushare 指数日线源")
            return []
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
        if not self.is_configured():
            return HealthStatus(healthy=False, message="未配置 TUSHARE_TOKEN")
        try:
            start = time.perf_counter()
            start_date = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
            bars = self.fetch_index_daily("000001", start_date=start_date)
            elapsed = (time.perf_counter() - start) * 1000
            ok = len(bars) > 0
            return HealthStatus(
                healthy=ok,
                message="tushare 指数接口可达" if ok else "tushare 指数接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
