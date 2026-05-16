from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.request import Request, urlopen

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus


@dataclass
class EastmoneyShareSnapshot:
    code: str
    price: float  # 当前价格，单位 元
    shares_total: float  # 总份额，单位 亿份
    aum: float  # 资产管理规模，单位 亿元


@dataclass
class EastmoneyFundInfo:
    code: str
    name_cn: str
    fund_full_name: str | None
    fund_company: str | None
    tracking_index_name: str | None
    tracking_index_code: str | None


class EastmoneyClient(BaseDataClient):
    """东方财富数据客户端。

    通过东方财富 HTTP 接口拉取 ETF 基金基本信息和份额快照数据。
    """

    source_name = "eastmoney"
    base_url = "https://push2.eastmoney.com/api/qt/stock/get"
    fund_info_url = "https://fundmobapi.eastmoney.com/FundMApi/FundBaseInfo"
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
        """拉取 ETF 基金基本信息。

        Args:
            code: ETF 代码，如 510300

        Returns:
            EastmoneyFundInfo，未找到时返回 None
        """
        endpoint = "FundBaseInfo"
        url = f"{self.fund_info_url}?FCODE={code}&deviceid=Wap&plat=Wap&product=EFund&version=6.3.8"
        self._log_request(endpoint, {"code": code})
        start = time.perf_counter()
        try:
            request = Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"}
            )
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            d = payload.get("Datas") or {}
            if not d or not d.get("SHORTNAME"):
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return None
            result = EastmoneyFundInfo(
                code=code,
                name_cn=d["SHORTNAME"],
                fund_full_name=d.get("FULLNAME") or d["SHORTNAME"],
                fund_company=d.get("JJGS") or None,
                tracking_index_name=d.get("ZSZS") or None,
                tracking_index_code=d.get("ZSZSBM") or None,
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, 1, elapsed)
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def fetch_share_snapshot(
        self, code: str, exchange: str | None = None
    ) -> EastmoneyShareSnapshot | None:
        """拉取 ETF 份额快照（含价格、总份额、AUM）。

        Args:
            code: ETF 代码，如 510300
            exchange: 交易所 SSE/SZSE，None 时从 market_map 推断

        Returns:
            EastmoneyShareSnapshot，无数据时返回 None
        """
        endpoint = "qt/stock/get"
        if exchange is not None:
            market = self.exchange_to_market(exchange)
        else:
            market = self.market_map.get(code)
        if market is None:
            self._logger.warning("无法推断 %s 的市场编码", code)
            return None
        url = f"{self.base_url}?secid={market}.{code}&fields=f43,f57,f58,f116"
        self._log_request(endpoint, {"code": code, "market": market})
        start = time.perf_counter()
        try:
            request = Request(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
            )
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("data") or {}
            if not data:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return None
            price = data.get("f43", 0) / 1000.0
            aum = data.get("f116", 0) / 1e8
            if price <= 0:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return None
            shares_total = data.get("f116", 0) / price / 1e8
            result = EastmoneyShareSnapshot(
                code=code,
                price=round(price, 3),
                shares_total=round(shares_total, 2),
                aum=round(aum, 1),
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, 1, elapsed)
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def health_check(self) -> HealthStatus:
        """通过拉取 510300 份额快照检测连通性。"""
        try:
            start = time.perf_counter()
            snapshot = self.fetch_share_snapshot("510300")
            elapsed = (time.perf_counter() - start) * 1000
            ok = snapshot is not None
            return HealthStatus(
                healthy=ok,
                message="东方财富接口可达" if ok else "东方财富接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
