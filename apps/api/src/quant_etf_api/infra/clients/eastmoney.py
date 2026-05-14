from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.request import Request, urlopen


@dataclass
class EastmoneyShareSnapshot:
    code: str
    price: float
    shares_total: float
    aum: float


class EastmoneyClient:
    base_url = "https://push2.eastmoney.com/api/qt/stock/get"
    market_map = {
        "510300": "1",
        "510310": "1",
        "510330": "1",
        "159919": "0",
        "510050": "1",
        "510500": "1",
        "512100": "1",
    }

    def fetch_share_snapshot(self, code: str) -> EastmoneyShareSnapshot | None:
        market = self.market_map.get(code)
        if market is None:
            return None
        url = f"{self.base_url}?secid={market}.{code}&fields=f43,f57,f58,f116"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data") or {}
        if not data:
            return None
        price = data.get("f43", 0) / 1000.0
        aum = data.get("f116", 0) / 1e8
        if price <= 0:
            return None
        shares_total = data.get("f116", 0) / price / 1e8
        return EastmoneyShareSnapshot(code=code, price=round(price, 3), shares_total=round(shares_total, 2), aum=round(aum, 1))
