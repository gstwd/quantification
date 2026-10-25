"""日频市场指标客户端：Tushare（两融 / 股指期货 / 股指期权）与 AkShare（10 年国债收益率）。

客户端只负责拉取与归一化，返回 :class:`MarketIndicatorPoint`；增量区间、写库与失败
处理由 ``DataManagementService`` 编排。Tushare 中债收益率曲线（yc_cb）需要单独权限，
10 年国债收益率改用 AkShare ``bond_zh_us_rate``（中国国债收益率 10 年，%）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Any

from quant_etf_api.domain.market_data.indicators import (
    FUTURES_PRODUCTS,
    OPTION_PRODUCTS,
    MarketIndicatorPoint,
)
from quant_etf_api.infra.clients.base import BaseDataClient
from quant_etf_api.infra.clients.retry_decorator import with_retry
from quant_etf_api.infra.clients.tushare_market import (
    _parse_yyyymmdd as _to_date,
)
from quant_etf_api.infra.clients.tushare_market import (
    _to_float,
    _TushareBaseMixin,
)

# 两融金额由元换算为亿元
_YUAN_PER_YI = 1e8
# 两融汇总只取沪深两市（北交所 2021 年起才有，纳入会让序列出现结构性跳变）
_MARGIN_EXCHANGES = ("SSE", "SZSE")
# 单次 Tushare 调用的尝试次数（期权逐日拉取与分组回补共用）
_OPTION_DAY_ATTEMPTS = 3


def _year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """把区间切成按自然年的子区间（控制单次调用的返回行数）。"""
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, date(cursor.year, 12, 31))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _ymd(day: date) -> str:
    """日期 → Tushare 的 YYYYMMDD。"""
    return day.strftime("%Y%m%d")


class TushareMarketIndicatorClient(_TushareBaseMixin, BaseDataClient):
    """Tushare 日频市场指标客户端（margin / fut_daily / fut_mapping / fut_basic / opt_*）。"""

    source_name = "tushare_market_indicator"

    def fetch_margin(self, start: date, end: date) -> list[MarketIndicatorPoint]:
        """沪深两市融资余额、融资买入额、融券余额（亿元）。

        Args:
            start: 起始交易日（含）。
            end: 截止交易日（含）。

        Returns:
            按交易日汇总后的观测点；某交易日缺任一交易所时跳过该日（避免半边加总）。
        """
        totals: dict[date, dict[str, dict[str, float]]] = {}
        for chunk_start, chunk_end in _year_chunks(start, end):
            df = self._fetch_retry("margin", start_date=_ymd(chunk_start), end_date=_ymd(chunk_end))
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                exchange = str(row.get("exchange_id") or "")
                day = _to_date(row.get("trade_date"))
                if exchange not in _MARGIN_EXCHANGES or day is None:
                    continue
                totals.setdefault(day, {})[exchange] = {
                    "rzye": _to_float(row.get("rzye")),
                    "rzmre": _to_float(row.get("rzmre")),
                    "rqye": _to_float(row.get("rqye")),
                }
        points: list[MarketIndicatorPoint] = []
        for day, by_exchange in sorted(totals.items()):
            if set(by_exchange) != set(_MARGIN_EXCHANGES):
                continue
            for field in ("rzye", "rzmre", "rqye"):
                values = [item[field] for item in by_exchange.values()]
                if any(v is None for v in values):
                    continue
                total = sum(v for v in values if v is not None) / _YUAN_PER_YI
                points.append(MarketIndicatorPoint(f"margin_{field}", day, total, "tushare"))
        return points

    def fetch_futures(self, product: str, start: date, end: date) -> list[MarketIndicatorPoint]:
        """股指期货主力连续收盘与当日主力合约剩余自然日。

        Args:
            product: 品种（if / ih / ic / im）。
            start: 起始交易日（含）。
            end: 截止交易日（含）。

        Returns:
            ``fut_<品种>_close`` 与 ``fut_<品种>_dte`` 观测点。
        """
        continuous, _spot = FUTURES_PRODUCTS[product]
        delist = self._contract_delist_dates()
        points: list[MarketIndicatorPoint] = []
        for chunk_start, chunk_end in _year_chunks(start, end):
            daily = self._fetch_retry(
                "fut_daily",
                ts_code=continuous,
                start_date=_ymd(chunk_start),
                end_date=_ymd(chunk_end),
            )
            if daily is not None and not daily.empty:
                for _, row in daily.iterrows():
                    day = _to_date(row.get("trade_date"))
                    close = _to_float(row.get("close"))
                    if day is not None and close is not None and close > 0:
                        points.append(
                            MarketIndicatorPoint(f"fut_{product}_close", day, close, "tushare")
                        )
            mapping = self._fetch_retry(
                "fut_mapping",
                ts_code=continuous,
                start_date=_ymd(chunk_start),
                end_date=_ymd(chunk_end),
            )
            if mapping is not None and not mapping.empty:
                for _, row in mapping.iterrows():
                    day = _to_date(row.get("trade_date"))
                    expiry = delist.get(str(row.get("mapping_ts_code") or ""))
                    if day is not None and expiry is not None and expiry >= day:
                        points.append(
                            MarketIndicatorPoint(
                                f"fut_{product}_dte", day, float((expiry - day).days), "tushare"
                            )
                        )
        return points

    def fetch_option_pcr(self, trade_dates: Iterable[date]) -> list[MarketIndicatorPoint]:
        """中金所股指期权逐日 PCR（认沽 / 认购，成交量与持仓量两种口径）。

        每个交易日一次 opt_daily 调用覆盖全部品种；认购或认沽合计为 0 的品种当日不出值。

        Args:
            trade_dates: 需要拉取的交易日。

        Returns:
            ``opt_<品种>_pcr_vol`` / ``opt_<品种>_pcr_oi`` 观测点。
        """
        contracts = self._option_contracts()
        products = {code: product for product, code in OPTION_PRODUCTS.items()}
        points: list[MarketIndicatorPoint] = []
        self.failed_option_days: list[date] = []
        for day in sorted(set(trade_dates)):
            df = self._fetch_day_with_retry(day)
            if df is None or df.empty:
                continue
            sums: dict[str, dict[str, float]] = {}
            for _, row in df.iterrows():
                info = contracts.get(str(row.get("ts_code") or ""))
                if info is None or info[1] not in products:
                    continue
                call_put, underlying = info
                bucket = sums.setdefault(products[underlying], {})
                for field in ("vol", "oi"):
                    value = _to_float(row.get(field)) or 0.0
                    key = f"{call_put}_{field}"
                    bucket[key] = bucket.get(key, 0.0) + value
            for product, bucket in sums.items():
                for field in ("vol", "oi"):
                    calls = bucket.get(f"C_{field}", 0.0)
                    puts = bucket.get(f"P_{field}", 0.0)
                    if calls > 0:
                        points.append(
                            MarketIndicatorPoint(
                                f"opt_{product}_pcr_{field}", day, puts / calls, "tushare"
                            )
                        )
        return points

    def _fetch_retry(self, endpoint: str, **params: Any) -> Any:
        """带重试的 Tushare 调用：偶发超时不应让整段分组回补作废。

        Args:
            endpoint: 接口名。
            **params: 接口参数。

        Returns:
            DataFrame。

        Raises:
            最后一次尝试的异常（重试耗尽时原样上抛）。
        """
        for attempt in range(_OPTION_DAY_ATTEMPTS):
            try:
                return self._fetch(endpoint, **params)
            except Exception:  # noqa: BLE001
                if attempt == _OPTION_DAY_ATTEMPTS - 1:
                    raise
        return None

    def _fetch_day_with_retry(self, day: date) -> Any:
        """拉取某日期权行情，失败重试；仍失败时记入 ``failed_option_days`` 并跳过该日。

        逐日调用上千次时偶发超时不应让整段回补作废。

        Args:
            day: 交易日。

        Returns:
            DataFrame；连续失败时为 None。
        """
        for attempt in range(_OPTION_DAY_ATTEMPTS):
            try:
                return self._fetch("opt_daily", exchange="CFFEX", trade_date=_ymd(day))
            except Exception:  # noqa: BLE001
                if attempt == _OPTION_DAY_ATTEMPTS - 1:
                    self.failed_option_days.append(day)
        return None

    def _contract_delist_dates(self) -> dict[str, date]:
        """中金所期货合约 → 最后交易日（用于主力合约剩余天数）。"""
        if not hasattr(self, "_delist_cache"):
            df = self._fetch_retry(
                "fut_basic", exchange="CFFEX", fut_type="1", fields="ts_code,delist_date"
            )
            cache: dict[str, date] = {}
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    expiry = _to_date(row.get("delist_date"))
                    if expiry is not None:
                        cache[str(row.get("ts_code"))] = expiry
            self._delist_cache = cache
        return self._delist_cache

    def _option_contracts(self) -> dict[str, tuple[str, str]]:
        """中金所期权合约 → (C/P, 标的代码)。"""
        if not hasattr(self, "_option_cache"):
            df = self._fetch_retry("opt_basic", exchange="CFFEX", fields="ts_code,call_put,opt_code")
            cache: dict[str, tuple[str, str]] = {}
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    call_put = str(row.get("call_put") or "").upper()
                    if call_put in ("C", "P"):
                        cache[str(row.get("ts_code"))] = (call_put, str(row.get("opt_code") or ""))
            self._option_cache = cache
        return self._option_cache


class AkShareBondYieldClient(BaseDataClient):
    """AkShare 中美国债收益率客户端（只取中国 10 年期）。"""

    source_name = "akshare_bond_yield"

    @with_retry()
    def fetch_cgb10y(self, start: date) -> list[MarketIndicatorPoint]:
        """中国 10 年期国债收益率（%）。

        Args:
            start: 起始日期（含）。

        Returns:
            ``cgb10y`` 观测点（缺值的日期跳过）。
        """
        import akshare as ak

        self._log_request("bond_zh_us_rate", {"start_date": _ymd(start)})
        df: Any = ak.bond_zh_us_rate(start_date=_ymd(start))
        points: list[MarketIndicatorPoint] = []
        if df is None or df.empty:
            return points
        for _, row in df.iterrows():
            day = _to_date(row.get("日期"))
            value = _to_float(row.get("中国国债收益率10年"))
            if day is not None and value is not None and day >= start:
                points.append(MarketIndicatorPoint("cgb10y", day, value, "akshare"))
        return points


def option_trade_dates(calendar: Sequence[date], start: date, end: date) -> list[date]:
    """期权逐日拉取的交易日（只取区间内交易日，避免对休市日空调）。

    Args:
        calendar: 交易日历（升序）。
        start: 起始日（含）。
        end: 截止日（含）。

    Returns:
        交易日列表。
    """
    return [day for day in calendar if start <= day <= end]
