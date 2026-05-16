from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.request import Request, urlopen

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus


@dataclass
class TencentDailyBar:
    trade_date: str
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float


class TencentClient(BaseDataClient):
    """腾讯财经日线行情客户端。

    通过腾讯财经 HTTP 接口拉取 ETF/指数日线 OHLCV 数据（前复权）。
    """

    source_name = "tencent"
    base_url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    @staticmethod
    def market_prefix(code: str) -> str:
        """根据代码推断交易所前缀，sh=上交所，sz=深交所。"""
        return "sh" if code.startswith(("51", "56", "0")) else "sz"

    def fetch_daily_bars(self, code: str, limit: int = 60) -> list[TencentDailyBar]:
        """拉取 ETF 日线行情数据（前复权）。

        Args:
            code: ETF 代码，如 510300
            limit: 拉取条数，默认 60

        Returns:
            按交易日升序排列的日线数据列表
        """
        endpoint = "fqkline"
        prefix = self.market_prefix(code)
        url = f"{self.base_url}?param={prefix}{code},day,,,{limit},qfq"
        self._log_request(endpoint, {"code": code, "limit": limit})
        start = time.perf_counter()
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            series = payload.get("data", {}).get(f"{prefix}{code}", {})
            rows = series.get("qfqday") or series.get("day") or []
            result = [
                TencentDailyBar(
                    trade_date=row[0],
                    open_price=float(row[1]),
                    close_price=float(row[2]),
                    high_price=float(row[3]),
                    low_price=float(row[4]),
                    volume=float(row[5]),
                )
                for row in rows
                if len(row) >= 6 and row[0]
            ]
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(result), elapsed)
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def health_check(self) -> HealthStatus:
        """通过拉取 510300 最近 1 条数据检测连通性。"""
        try:
            start = time.perf_counter()
            bars = self.fetch_daily_bars("510300", limit=1)
            elapsed = (time.perf_counter() - start) * 1000
            ok = len(bars) > 0
            return HealthStatus(
                healthy=ok,
                message="腾讯行情接口可达" if ok else "腾讯行情接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
