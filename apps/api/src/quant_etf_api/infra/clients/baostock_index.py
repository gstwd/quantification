from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta

import pandas as pd

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus
from quant_etf_api.infra.clients.index_daily_common import (
    IndexDailyBar,
    _build_index_bars,
    _incremental_start_date,
    index_code_market_prefix,
)
from quant_etf_api.infra.clients.retry_decorator import with_retry

# Baostock 为全局登录状态，并发调用需串行化（与 IngestService 多线程摄取配合）
_BAOSTOCK_LOGIN_LOCK = threading.Lock()

# Baostock 指数日线默认起点：其指数数据自 1990 年起
_BAOSTOCK_HISTORY_START = "1990-01-01"


class BaostockIndexClient(BaseDataClient):
    """Baostock 指数日线客户端（基于 baostock SDK）。

    仅使用 Baostock 的历史 K 线接口（query_history_k_data_plus，frequency='d'），
    不涉及选股、财务、复权等其余功能。数据来自 Baostock：
    - 免费、无需 Token，覆盖上证/深证主流指数（代码带 sh./sz. 前缀）；
    - 返回字段均为字符串，本客户端统一数值化；
    - 成交量单位股 → 归一化为手（÷100），成交额单位元保持不变，
      与 index_daily_bar 表口径保持一致；
    - 指数日线使用不复权（adjustflag='3'），指数点位本身无复权概念。
    """

    source_name = "baostock_index"

    @staticmethod
    def _fmt_date(value: str | None) -> str:
        """将 'YYYYMMDD' 转为 Baostock 所需的 'YYYY-MM-DD' 参数格式。

        Args:
            value: 日期字符串，如 '20260101'。

        Returns:
            'YYYY-MM-DD' 格式字符串。
        """
        if value is None:
            return _BAOSTOCK_HISTORY_START
        return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")

    @staticmethod
    def _bs_code(index_code: str) -> str:
        """将指数代码转为 Baostock 格式（sh.000300 / sz.399001）。

        Args:
            index_code: 指数代码，如 000300。

        Returns:
            Baostock 代码，如 'sh.000300'。
        """
        return f"{index_code_market_prefix(index_code)}.{index_code}"

    @with_retry()
    def _fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过 Baostock 拉取指数日线。

        Args:
            index_code: 指数代码，如 000300。
            start_date: 起始日 'YYYYMMDD'，None 表示 1990-01-01。
            end_date: 结束日 'YYYYMMDD'，None 表示今天。

        Returns:
            按日期升序排列的日线数据列表。
        """
        endpoint = "query_history_k_data_plus"
        code = self._bs_code(index_code)
        self._log_request(endpoint, {"index_code": index_code, "code": code})
        start = time.perf_counter()
        try:
            import baostock as bs

            with _BAOSTOCK_LOGIN_LOCK:
                lg = bs.login()
                if lg.error_code != "0":
                    raise RuntimeError(f"Baostock 登录失败: {lg.error_msg}")
                try:
                    rs = bs.query_history_k_data_plus(
                        code=code,
                        fields="date,open,high,low,close,volume,amount,pctChg",
                        start_date=self._fmt_date(start_date),
                        end_date=self._fmt_date(end_date)
                        if end_date
                        else date.today().strftime("%Y-%m-%d"),
                        frequency="d",
                        adjustflag="3",
                    )
                    if rs.error_code != "0":
                        raise RuntimeError(f"Baostock 查询失败: {rs.error_msg}")
                    rows: list[list[str]] = []
                    while rs.next():
                        rows.append(rs.get_row_data())
                finally:
                    bs.logout()

            if not rows:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return []

            df = pd.DataFrame(rows, columns=rs.fields)
            # Baostock 缺失值以空字符串表示，统一数值化；
            # 成交量单位股 → 手（÷100），成交额单位元保持不变
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce") / 100.0
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            bars = _build_index_bars(
                df,
                date_col="date",
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
        """拉取指数日线 OHLCV 数据（baostock 单源入口）。

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
                message="baostock 指数接口可达" if ok else "baostock 指数接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
