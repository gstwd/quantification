from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.request import Request, urlopen


@dataclass
class EastmoneyShareSnapshot:
    code: str
    price: float        # 当前价格，单位 元
    shares_total: float # 总份额，单位 亿份
    aum: float          # 资产管理规模，单位 亿元


@dataclass
class EastmoneyFundInfo:
    code: str
    name_cn: str
    fund_full_name: str | None
    fund_company: str | None
    tracking_index_name: str | None
    tracking_index_code: str | None


class EastmoneyClient:
    base_url = "https://push2.eastmoney.com/api/qt/stock/get"
    fund_info_url = "https://fundmobapi.eastmoney.com/FundMApi/FundBaseInfo"
    # 已知 ETF 的市场编码缓存（1=上交所，0=深交所），作为兜底
    market_map = {
        "510300": "1",
        "510310": "1",
        "510330": "1",
        "159919": "0",
        "510050": "1",
        "510500": "1",
        "512100": "1",
    }

    @staticmethod
    def exchange_to_market(exchange: str) -> str:
        return "1" if exchange == "SSE" else "0"

    def fetch_fund_info(self, code: str) -> EastmoneyFundInfo | None:
        url = (
            f"{self.fund_info_url}?FCODE={code}"
            "&deviceid=Wap&plat=Wap&product=EFund&version=6.3.8"
        )
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"})
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        d = payload.get("Datas") or {}
        if not d or not d.get("SHORTNAME"):
            return None
        return EastmoneyFundInfo(
            code=code,
            name_cn=d["SHORTNAME"],
            fund_full_name=d.get("FULLNAME") or d["SHORTNAME"],
            fund_company=d.get("JJGS") or None,
            tracking_index_name=d.get("ZSZS") or None,
            tracking_index_code=d.get("ZSZSBM") or None,
        )

    def fetch_share_snapshot(self, code: str, exchange: str | None = None) -> EastmoneyShareSnapshot | None:
        if exchange is not None:
            market = self.exchange_to_market(exchange)
        else:
            market = self.market_map.get(code)
        if market is None:
            return None
        # 请求字段：f43=价格×1000，f57=代码，f58=名称，f116=总市值（元）
        url = f"{self.base_url}?secid={market}.{code}&fields=f43,f57,f58,f116"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data") or {}
        if not data:
            return None
        # f43 为价格乘以 1000 的整数，需除以 1000 还原
        price = data.get("f43", 0) / 1000.0
        # f116 为总市值（元），除以 1e8 转换为亿元
        aum = data.get("f116", 0) / 1e8
        if price <= 0:
            return None
        # 总份额 = 总市值 / 单价 / 1e8，单位亿份
        shares_total = data.get("f116", 0) / price / 1e8
        return EastmoneyShareSnapshot(code=code, price=round(price, 3), shares_total=round(shares_total, 2), aum=round(aum, 1))
