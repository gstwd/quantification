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
    prev_close_price: float | None = None
    change_pct: float | None = None


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
    "000009": "上证380",
    "000010": "上证180",
    "399330": "深证100",
    "399673": "创业板50",
    "399324": "深证红利",
    "000015": "上证红利",
    "000903": "中证100",
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
            # 逐日计算涨跌幅（第一根无前收盘，保持 None）
            for i in range(1, len(bars)):
                prev_close = bars[i - 1].close_price
                if prev_close and prev_close != 0:
                    bars[i].prev_close_price = prev_close
                    bars[i].change_pct = round(
                        (bars[i].close_price - prev_close) / prev_close * 100, 4
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
        中证1000、中证800、上证380、上证180、深证100、创业板50、
        深证红利、上证红利、中证100），其他指数返回空列表。

        百分位计算逻辑：对于每个交易日，统计历史所有交易日中 PE/PB 值
        小于当前值的比例，得到 0-100 的历史分位数。

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

        # 重试机制：legulegu.com 偶发 CSRF token 获取失败，最多重试 3 次
        max_retries = 3
        pe_df = None
        pb_df = None
        last_error = None

        for attempt in range(max_retries):
            try:
                pe_df = ak.stock_index_pe_lg(symbol=name)
                pb_df = ak.stock_index_pb_lg(symbol=name)
                break
            except (AttributeError, Exception) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_seconds = 2 ** attempt
                    self._logger.info(
                        "指数 %s 估值拉取失败（第 %d 次），%d 秒后重试: %s",
                        index_code, attempt + 1, wait_seconds, e
                    )
                    time.sleep(wait_seconds)

        if pe_df is None or pb_df is None:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error("stock_index_pe_lg+pb", last_error, elapsed)
            raise last_error

        # 按日期建立 PB 查找表
        pb_map: dict[date, float | None] = {}
        for _, row in pb_df.iterrows():
            d = row["日期"]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            pb_val = row.get("市净率")
            pb_map[d] = float(pb_val) if pb_val is not None else None

        # 收集所有 PE 和 PB 值用于计算百分位
        pe_values: list[tuple[date, float]] = []
        pb_values: list[tuple[date, float]] = []

        for _, row in pe_df.iterrows():
            d = row["日期"]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            pe_val = row.get("静态市盈率")
            if pe_val is not None:
                try:
                    pe_values.append((d, float(pe_val)))
                except (ValueError, TypeError):
                    pass

        for d, pb_val in pb_map.items():
            if pb_val is not None:
                pb_values.append((d, pb_val))

        # 按日期排序
        pe_values.sort(key=lambda x: x[0])
        pb_values.sort(key=lambda x: x[0])

        # 计算历史百分位：对于每个交易日，统计之前所有交易日中值小于当前值的比例
        def _calc_percentile(data: list[tuple[date, float]]) -> dict[date, float]:
            """计算每个交易日的历史百分位。"""
            result: dict[date, float] = {}
            for i, (d, val) in enumerate(data):
                # 统计前 i 个交易日中值小于当前值的数量
                count_less = sum(1 for _, prev_val in data[:i] if prev_val < val)
                percentile = round(count_less / i * 100, 2) if i > 0 else 50.0
                result[d] = percentile
            return result

        pe_percentile_map = _calc_percentile(pe_values)
        pb_percentile_map = _calc_percentile(pb_values)

        # 构建结果
        results: list[IndexValuation] = []
        for _, row in pe_df.iterrows():
            d = row["日期"]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            pe_val = row.get("静态市盈率")
            pb_val = pb_map.get(d)
            results.append(
                IndexValuation(
                    trade_date=d,
                    pe=float(pe_val) if pe_val is not None else None,
                    pe_percentile=pe_percentile_map.get(d),
                    pb=pb_val,
                    pb_percentile=pb_percentile_map.get(d),
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
