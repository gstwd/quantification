from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime

import akshare as ak

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus


@dataclass
class IndexDailyBar:
    """指数日线行情数据结构。"""

    trade_date: date
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float
    turnover: float


@dataclass
class IndexValuation:
    """指数估值数据结构（PE/PB 及历史分位）。"""

    trade_date: date
    pe: float | None
    pe_percentile: float | None
    pb: float | None
    pb_percentile: float | None
    dividend_yield: float | None


# 从 index_code 到 legulegu PE/PB 所需中文名的映射
# AkShare stock_index_pe_lg / stock_index_pb_lg 支持的全部 12 个指数
_PE_PB_NAME_MAP: dict[str, str] = {
    "000300": "沪深300",
    "000016": "上证50",
    "000905": "中证500",
    "000852": "中证1000",
    "000906": "中证800",
    "000009": "中证380",
    "000010": "中证180",
    "399330": "中证100",
    "399673": "创业板50",
    "399324": "中证红利",
    "000015": "上证红利",
    "000903": "上证100",
}


def _index_code_to_ak_symbol(index_code: str) -> str:
    """将 index_code 转为 AkShare 腾讯日线接口的 symbol。

    Args:
        index_code: 指数代码，如 000300、399001

    Returns:
        AkShare symbol，如 sh000300、sz399001
    """
    if index_code.startswith(("0", "51", "56")):
        return f"sh{index_code}"
    return f"sz{index_code}"


_INDEX_NAME_CACHE: dict[str, str] | None = None


class AkShareIndexClient(BaseDataClient):
    """指数行情与估值客户端（基于 AkShare SDK）。

    封装 akshare 的指数日线（腾讯源）和指数 PE/PB 估值（乐股乐源）接口，
    以及指数名称查询（聚宽数据源）。
    """

    source_name = "akshare_index"

    # ------------------------------------------------------------------
    # 指数名称查询
    # ------------------------------------------------------------------

    def fetch_index_name(self, index_code: str) -> str | None:
        """根据指数代码查询中文名称。

        使用 akshare index_stock_info（聚宽数据源）获取全量指数列表，
        结果缓存在模块级变量中，进程生命周期内有效。

        Args:
            index_code: 指数代码，如 '000300'

        Returns:
            指数中文名称（如 '沪深300'），未找到时返回 None
        """
        global _INDEX_NAME_CACHE
        if _INDEX_NAME_CACHE is None:
            endpoint = "index_stock_info"
            self._log_request(endpoint, {})
            start = time.perf_counter()
            try:
                df = ak.index_stock_info()
                _INDEX_NAME_CACHE = dict(zip(df["index_code"], df["display_name"]))
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, len(_INDEX_NAME_CACHE), elapsed)
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_error(endpoint, e, elapsed)
                return None
        return _INDEX_NAME_CACHE.get(index_code)

    def find_index_code_by_name(self, name: str) -> str | None:
        """根据指数名称反查指数代码。

        对输入名称去除常见后缀（如 '指数'、'(价格)'）后进行模糊匹配。

        Args:
            name: 指数名称，如 '沪深300指数' 或 '创业板指数(价格)'

        Returns:
            匹配到的指数代码，未找到时返回 None
        """
        global _INDEX_NAME_CACHE
        if _INDEX_NAME_CACHE is None:
            self.fetch_index_name("000001")
        if _INDEX_NAME_CACHE is None:
            return None

        import re

        cleaned = re.sub(r"[（(].*?[）)]", "", name).replace("指数", "").strip()
        if not cleaned:
            return None

        for code, display_name in _INDEX_NAME_CACHE.items():
            if display_name == cleaned or display_name == name:
                return code

        for code, display_name in _INDEX_NAME_CACHE.items():
            if cleaned in display_name or display_name in cleaned:
                return code

        return None

    # ------------------------------------------------------------------
    # 指数日线
    # ------------------------------------------------------------------

    def fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """拉取指数日线 OHLCV 数据。

        Args:
            index_code: 指数代码，如 000300
            start_date: 起始日 'YYYYMMDD'，None 表示最早
            end_date: 结束日 'YYYYMMDD'，None 表示最新

        Returns:
            按日期升序排列的日线数据列表
        """
        endpoint = "stock_zh_index_daily_tx"
        symbol = _index_code_to_ak_symbol(index_code)
        self._log_request(endpoint, {"index_code": index_code, "symbol": symbol})
        start = time.perf_counter()
        try:
            df = ak.stock_zh_index_daily_tx(
                symbol=symbol, start_date=start_date or "", end_date=end_date or ""
            )
            bars: list[IndexDailyBar] = []
            for _, row in df.iterrows():
                bar_date = row["date"]
                if isinstance(bar_date, str):
                    bar_date = datetime.strptime(bar_date, "%Y-%m-%d").date()
                bars.append(
                    IndexDailyBar(
                        trade_date=bar_date,
                        open_price=float(row["open"]),
                        close_price=float(row["close"]),
                        high_price=float(row["high"]),
                        low_price=float(row["low"]),
                        volume=float(row.get("volume", 0)),
                        turnover=float(row["amount"]),
                    )
                )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(bars), elapsed)
            return bars
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    # ------------------------------------------------------------------
    # 指数估值 PE/PB
    # ------------------------------------------------------------------

    def fetch_index_valuation(self, index_code: str) -> list[IndexValuation]:
        """拉取指数 PE/PB 估值历史数据。

        支持 legulegu 覆盖的 12 个指数（沪深300、上证50、中证500、
        中证1000、中证800、中证380、中证180、中证100、创业板50、
        中证红利、上证红利、上证100），其他指数返回空列表。

        Args:
            index_code: 指数代码

        Returns:
            按日期升序排列的估值数据列表
        """
        name = _PE_PB_NAME_MAP.get(index_code)
        if name is None:
            self._logger.info(
                "%s 不支持估值数据，仅支持 %s", index_code, list(_PE_PB_NAME_MAP.keys())
            )
            return []

        start = time.perf_counter()
        self._log_request("stock_index_pe_lg+pb", {"index_code": index_code, "name": name})
        try:
            pe_df = ak.stock_index_pe_lg(symbol=name)
            pb_df = ak.stock_index_pb_lg(symbol=name)
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error("stock_index_pe_lg+pb", e, elapsed)
            raise

        # 按日期建立 PB 查找表
        pb_map: dict[date, tuple[float | None, float | None]] = {}
        for _, row in pb_df.iterrows():
            d = row["日期"]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            pb_map[d] = (
                float(row["市净率"]) if row.get("市净率") is not None else None,
                float(row["市净率百分位"]) if row.get("市净率百分位") is not None else None,
            )

        results: list[IndexValuation] = []
        for _, row in pe_df.iterrows():
            d = row["日期"]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            pb_val, pb_pct = pb_map.get(d, (None, None))
            pe_val = row.get("静态市盈率")
            pe_pct = row.get("静态市盈率百分位")
            results.append(
                IndexValuation(
                    trade_date=d,
                    pe=float(pe_val) if pe_val is not None else None,
                    pe_percentile=float(pe_pct) if pe_pct is not None else None,
                    pb=pb_val,
                    pb_percentile=pb_pct,
                    dividend_yield=None,  # legulegu 源无股息率
                )
            )

        elapsed = (time.perf_counter() - start) * 1000
        self._log_response("stock_index_pe_lg+pb", len(results), elapsed)
        return results

    # ------------------------------------------------------------------
    # 健康检测
    # ------------------------------------------------------------------

    def health_check(self) -> HealthStatus:
        """通过拉取上证指数最近数据检测连通性。"""
        try:
            start = time.perf_counter()
            bars = self.fetch_index_daily("000001")
            elapsed = (time.perf_counter() - start) * 1000
            ok = len(bars) > 0
            return HealthStatus(
                healthy=ok,
                message="AkShare 指数接口可达" if ok else "AkShare 指数接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
