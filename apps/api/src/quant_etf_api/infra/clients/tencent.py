from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.request import Request, urlopen


@dataclass
class TencentDailyBar:
    trade_date: str
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float


class TencentClient:
    base_url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    @staticmethod
    def market_prefix(code: str) -> str:
        return "sh" if code.startswith(("51", "56", "0")) else "sz"

    def fetch_daily_bars(self, code: str, limit: int = 60) -> list[TencentDailyBar]:
        prefix = self.market_prefix(code)
        url = f"{self.base_url}?param={prefix}{code},day,,,{limit},qfq"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        series = payload.get("data", {}).get(f"{prefix}{code}", {}).get("day", [])
        return [
            TencentDailyBar(
                trade_date=row[0],
                open_price=float(row[1]),
                close_price=float(row[2]),
                high_price=float(row[3]),
                low_price=float(row[4]),
                volume=float(row[5]),
            )
            for row in series
            if len(row) >= 6 and row[0]
        ]
