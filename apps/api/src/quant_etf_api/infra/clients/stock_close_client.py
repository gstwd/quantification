"""个股收盘数据客户端（历史回填 + 当日快照，仅供数量占比扩散）。"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import akshare as ak
import pandas as pd

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus
from quant_etf_api.infra.clients.retry_decorator import with_retry


def _market_prefix(stock_code: str) -> str | None:
    """根据 A 股代码推断市场前缀（sh/sz/bj），未知返回 None。"""
    if stock_code.startswith(("60", "68", "90", "51", "58", "56")):
        return "sh"
    if stock_code.startswith(("00", "30", "12", "15", "16", "18")):
        return "sz"
    if stock_code.startswith(("43", "83", "87", "88", "92")):
        return "bj"
    return None


def _fmt_date(value: str) -> str:
    """将 'YYYYMMDD' 转为 baostock 所需的 'YYYY-MM-DD' 格式。

    Args:
        value: 日期字符串，如 '20130101'。

    Returns:
        'YYYY-MM-DD' 格式日期字符串。
    """
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"


class StockCloseClient(BaseDataClient):
    """个股收盘数据客户端。

    历史回填默认走 baostock（覆盖沪/深含退市历史），回退 AkShare 个股历史；
    每日增量用东方财富全 A 当日快照一次拉全市场最新价。
    """

    source_name = "stock_close"

    @with_retry()
    def fetch_history_baostock(
        self, stock_code: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """通过 baostock 拉取单只股票的历史收盘价。

        Args:
            stock_code: 6 位股票代码。
            start_date: 起始日 'YYYYMMDD'。
            end_date: 结束日 'YYYYMMDD'。

        Returns:
            按日期升序的 {trade_date, close} 字典列表。
        """
        import baostock as bs

        prefix = _market_prefix(stock_code)
        if prefix not in ("sh", "sz"):
            return []
        endpoint = "query_history_k_data_plus"
        self._log_request(endpoint, {"stock_code": stock_code})
        start = time.perf_counter()
        lg = bs.login()
        try:
            if lg.error_code != "0":
                raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
            rs = bs.query_history_k_data_plus(
                code=f"{prefix}.{stock_code}",
                fields="date,close",
                start_date=_fmt_date(start_date),
                end_date=_fmt_date(end_date),
                frequency="d",
                adjustflag="3",
            )
            if rs is None:
                raise RuntimeError("baostock 查询返回空结果")
            if rs.error_code != "0":
                raise RuntimeError(f"baostock 查询失败: {rs.error_msg}")
            rows: list[dict[str, Any]] = []
            while rs.next():
                data = rs.get_row_data()
                rows.append(
                    {
                        "trade_date": date.fromisoformat(data[0]),
                        "close": float(data[1]),
                    }
                )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(rows), elapsed)
            return rows
        finally:
            bs.logout()

    @with_retry()
    def fetch_history_akshare(
        self, stock_code: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """通过 AkShare 东方财富接口拉取单只股票历史收盘价（回退源）。"""
        endpoint = "stock_zh_a_hist"
        self._log_request(endpoint, {"stock_code": stock_code})
        start = time.perf_counter()
        df = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
        rows: list[dict[str, Any]] = []
        if df is not None and len(df) > 0:
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.date
            df["收盘"] = pd.to_numeric(df["收盘"], errors="coerce")
            for _, row in df.iterrows():
                trade_date = row["日期"]
                close = row["收盘"]
                if isinstance(trade_date, date) and pd.notna(close):
                    rows.append({"trade_date": trade_date, "close": float(close)})
        elapsed = (time.perf_counter() - start) * 1000
        self._log_response(endpoint, len(rows), elapsed)
        return rows

    def fetch_all_close_snapshot(self) -> list[dict[str, Any]]:
        """拉取当日全 A（沪深京）最新价快照。

        Returns:
            [{trade_date, stock_code, close}]，trade_date 取当天日期。
        """
        endpoint = "stock_zh_a_spot_em"
        self._log_request(endpoint, {})
        start = time.perf_counter()
        df = ak.stock_zh_a_spot_em()
        rows: list[dict[str, Any]] = []
        if df is not None and len(df) > 0:
            codes = df["代码"].astype(str).str.zfill(6)
            closes = pd.to_numeric(df["最新价"], errors="coerce")
            today = date.today()
            for code, close in zip(codes, closes):
                if pd.notna(close):
                    rows.append(
                        {
                            "trade_date": today,
                            "stock_code": code,
                            "close": float(close),
                            "source": "akshare_em",
                        }
                    )
        elapsed = (time.perf_counter() - start) * 1000
        self._log_response(endpoint, len(rows), elapsed)
        return rows

    def health_check(self) -> HealthStatus:
        """通过拉取当日快照检测连通性。"""
        try:
            rows = self.fetch_all_close_snapshot()
            return HealthStatus(
                healthy=len(rows) > 0,
                message="个股收盘接口可达" if rows else "个股收盘接口返回空数据",
                latency_ms=0.0,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
