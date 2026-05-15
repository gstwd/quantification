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
        # 上交所 ETF 代码以 51、56 或 0 开头，其余为深交所
        return "sh" if code.startswith(("51", "56", "0")) else "sz"

    def fetch_daily_bars(self, code: str, limit: int = 60) -> list[TencentDailyBar]:
        prefix = self.market_prefix(code)
        # qfq 参数表示前复权，保证历史价格可比
        url = f"{self.base_url}?param={prefix}{code},day,,,{limit},qfq"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        series = payload.get("data", {}).get(f"{prefix}{code}", {})
        # API 返回前复权数据用 "qfqday" 键，无前复权时回退到 "day"
        rows = series.get("qfqday") or series.get("day") or []
        return [
            TencentDailyBar(
                trade_date=row[0],
                open_price=float(row[1]),
                close_price=float(row[2]),
                high_price=float(row[3]),
                low_price=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
            if len(row) >= 6 and row[0]  # 过滤字段不足或日期为空的异常行
        ]
