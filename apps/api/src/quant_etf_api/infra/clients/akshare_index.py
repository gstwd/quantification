from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import akshare as ak

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus

# 东方财富 API 调用超时上限（秒），防止代理/网络问题导致长时间阻塞
_EM_API_TIMEOUT = 5


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

    分类规则：
    - 0/51/56 开头：上交所指数（上证综指、上证50 等）
    - 9 开头（930/931/932）：中证自定义指数，归属上海市场
    - 其他（399 开头等）：深交所指数

    Args:
        index_code: 指数代码，如 000300、399001、931743

    Returns:
        AkShare symbol，如 sh000300、sz399001、sh931743
    """
    if index_code.startswith(("0", "51", "56", "9")):
        return f"sh{index_code}"
    return f"sz{index_code}"


_INDEX_NAME_CACHE: dict[str, str] | None = None
_EM_INDEX_NAME_CACHE: dict[str, str] | None = None


class AkShareIndexClient(BaseDataClient):
    """指数行情与估值客户端（基于 AkShare SDK）。

    封装三层数据源降级策略：
    - 日线：腾讯源 → 中证指数官网 → 东方财富
    - 估值：乐股乐源 → 中证指数官网
    - 名称：聚宽 → 中证指数官网 → 东方财富
    """

    source_name = "akshare_index"

    # ------------------------------------------------------------------
    # 指数名称查询
    # ------------------------------------------------------------------

    @staticmethod
    def _call_with_timeout(fn, timeout: int = _EM_API_TIMEOUT):
        """在独立线程中执行函数，超时则抛出 TimeoutError。

        用于包装东方财富 API 调用，防止代理/网络问题导致长时间阻塞。

        Args:
            fn: 无参可调用对象
            timeout: 超时秒数

        Returns:
            函数返回值

        Raises:
            TimeoutError: 执行超时
        """
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            return future.result(timeout=timeout)

    def _fetch_index_name_from_csi(self, index_code: str) -> str | None:
        """通过中证指数官网查询指数中文名称。

        拉取近期历史数据，从返回的"指数中文全称"列提取名称。
        中证指数官网覆盖所有中证系指数（含 930/931/932 自定义指数）。

        Args:
            index_code: 指数代码，如 '931743'

        Returns:
            指数中文全称；失败时返回 None
        """
        endpoint = "stock_zh_index_hist_csindex"
        self._log_request(endpoint, {"index_code": index_code, "purpose": "name_lookup"})
        start = time.perf_counter()
        try:
            # 拉取近期数据确保覆盖最近交易日（用昨天避免当天数据未更新）
            yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
            df = ak.stock_zh_index_hist_csindex(
                symbol=index_code, start_date="20200101", end_date=yesterday
            )
            elapsed = (time.perf_counter() - start) * 1000
            if df is not None and len(df) > 0 and "指数中文全称" in df.columns:
                name = str(df.iloc[-1]["指数中文全称"])
                self._log_response(endpoint, 1, elapsed)
                return name
            self._log_response(endpoint, 0, elapsed)
            return None
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            return None

    def _load_em_index_names(self) -> dict[str, str]:
        """从东方财富加载全量指数名称映射（备用源）。

        ak.stock_zh_index_spot_em 返回实时行情快照，包含代码和名称列。
        调用受 _EM_API_TIMEOUT 超时保护，结果缓存在模块级变量中。

        Returns:
            index_code → display_name 映射字典；失败或超时时返回空字典
        """
        global _EM_INDEX_NAME_CACHE
        if _EM_INDEX_NAME_CACHE is not None:
            return _EM_INDEX_NAME_CACHE

        endpoint = "stock_zh_index_spot_em"
        self._log_request(endpoint, {})
        start = time.perf_counter()
        try:
            df = self._call_with_timeout(lambda: ak.stock_zh_index_spot_em())
            _EM_INDEX_NAME_CACHE = dict(zip(df["代码"], df["名称"]))
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(_EM_INDEX_NAME_CACHE), elapsed)
        except FutureTimeoutError:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(
                endpoint, TimeoutError(f"东方财富 API 超时（{_EM_API_TIMEOUT}s）"), elapsed
            )
            _EM_INDEX_NAME_CACHE = {}
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            _EM_INDEX_NAME_CACHE = {}
        return _EM_INDEX_NAME_CACHE

    def fetch_index_name(self, index_code: str) -> str | None:
        """根据指数代码查询中文名称。

        三级降级策略：
        1. 聚宽 index_stock_info（覆盖主流宽基指数，进程级缓存）
        2. 中证指数官网 stock_zh_index_hist_csindex（覆盖所有中证指数）
        3. 东方财富 stock_zh_index_spot_em（最终兜底，5 秒超时保护）

        Args:
            index_code: 指数代码，如 '000300'、'931743'

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
                _INDEX_NAME_CACHE = {}

        name = _INDEX_NAME_CACHE.get(index_code)
        if name is not None:
            return name

        # 聚宽源未命中，尝试中证指数官网
        name = self._fetch_index_name_from_csi(index_code)
        if name is not None:
            return name

        # 中证官网未命中，尝试东方财富备用源
        em_names = self._load_em_index_names()
        return em_names.get(index_code)

    def find_index_code_by_name(self, name: str) -> str | None:
        """根据指数名称反查指数代码。

        对输入名称去除常见后缀（如 '指数'、'(价格)'）后进行模糊匹配。
        依次搜索聚宽源和东方财富备用源的名称缓存。

        Args:
            name: 指数名称，如 '沪深300指数' 或 '创业板指数(价格)'

        Returns:
            匹配到的指数代码，未找到时返回 None
        """
        global _INDEX_NAME_CACHE
        if _INDEX_NAME_CACHE is None:
            self.fetch_index_name("000001")

        import re

        cleaned = re.sub(r"[（(].*?[）)]", "", name).replace("指数", "").strip()
        if not cleaned:
            return None

        # 构建合并后的名称→代码映射（聚宽优先，东方财富补充）
        merged: dict[str, str] = {}
        if _INDEX_NAME_CACHE:
            merged.update(_INDEX_NAME_CACHE)
        em_names = self._load_em_index_names()
        for code, display_name in em_names.items():
            if code not in merged:
                merged[code] = display_name

        for code, display_name in merged.items():
            if display_name == cleaned or display_name == name:
                return code

        for code, display_name in merged.items():
            if cleaned in display_name or display_name in cleaned:
                return code

        return None

    # ------------------------------------------------------------------
    # 指数日线
    # ------------------------------------------------------------------

    def _fetch_index_daily_csi(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过中证指数官网拉取指数日线（备用源）。

        使用 ak.stock_zh_index_hist_csindex，覆盖所有中证指数
        （含 930/931/932 自定义指数），同时返回 PE 估值数据。
        直接使用指数代码（无 sh/sz 前缀）。

        Args:
            index_code: 指数代码，如 931743
            start_date: 起始日 'YYYYMMDD'，None 表示 20100101
            end_date: 结束日 'YYYYMMDD'，None 表示今天

        Returns:
            按日期升序排列的日线数据列表
        """
        endpoint = "stock_zh_index_hist_csindex"
        self._log_request(endpoint, {"index_code": index_code})
        start = time.perf_counter()
        try:
            df = ak.stock_zh_index_hist_csindex(
                symbol=index_code,
                start_date=start_date or "20100101",
                end_date=end_date or date.today().strftime("%Y%m%d"),
            )
            bars: list[IndexDailyBar] = []
            for _, row in df.iterrows():
                bar_date = row["日期"]
                if isinstance(bar_date, str):
                    bar_date = datetime.strptime(bar_date, "%Y-%m-%d").date()
                bars.append(
                    IndexDailyBar(
                        trade_date=bar_date,
                        open_price=float(row["开盘"]),
                        close_price=float(row["收盘"]),
                        high_price=float(row["最高"]),
                        low_price=float(row["最低"]),
                        volume=float(row.get("成交量", 0) or 0),
                        turnover=float(row.get("成交额", 0) or 0),
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

    def _fetch_index_daily_em(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过东方财富源拉取指数日线（备用源）。

        当腾讯源不支持某个指数或返回空数据时，降级使用此方法。
        ak.stock_zh_index_daily_em 直接使用指数代码（无 sh/sz 前缀）。
        调用受 _EM_API_TIMEOUT 超时保护。

        Args:
            index_code: 指数代码，如 931743
            start_date: 起始日 'YYYYMMDD'，None 表示最早
            end_date: 结束日 'YYYYMMDD'，None 表示最新

        Returns:
            按日期升序排列的日线数据列表
        """
        endpoint = "stock_zh_index_daily_em"
        self._log_request(endpoint, {"index_code": index_code})
        start = time.perf_counter()
        try:
            df = self._call_with_timeout(
                lambda: ak.stock_zh_index_daily_em(
                    symbol=index_code,
                    start_date=start_date or "19700101",
                    end_date=end_date or "20500101",
                )
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
                        turnover=float(row.get("amount", 0)),
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
        except FutureTimeoutError:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(
                endpoint,
                TimeoutError(f"东方财富日线 API 超时（{_EM_API_TIMEOUT}s）"),
                elapsed,
            )
            raise
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """拉取指数日线 OHLCV 数据。

        三级降级策略：
        1. 腾讯源 stock_zh_index_daily_tx（覆盖主流交易所指数）
        2. 中证指数官网 stock_zh_index_hist_csindex（覆盖所有中证指数）
        3. 东方财富 stock_zh_index_daily_em（最终兜底，5 秒超时保护）

        Args:
            index_code: 指数代码，如 000300
            start_date: 起始日 'YYYYMMDD'，None 表示最早
            end_date: 结束日 'YYYYMMDD'，None 表示最新

        Returns:
            按日期升序排列的日线数据列表
        """
        # 第一级：腾讯源
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
            for i in range(1, len(bars)):
                prev_close = bars[i - 1].close_price
                if prev_close and prev_close != 0:
                    bars[i].prev_close_price = prev_close
                    bars[i].change_pct = round(
                        (bars[i].close_price - prev_close) / prev_close * 100, 4
                    )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(bars), elapsed)
            if bars:
                return bars
            # 腾讯源空数据，降级到中证官网
            self._logger.info("腾讯源对指数 %s 返回空数据，降级到中证指数官网", index_code)
            return self._fetch_index_daily_csi(index_code, start_date, end_date)
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            # 腾讯源失败 → 第二级：中证指数官网
            self._logger.info("腾讯源对指数 %s 拉取失败: %s，降级到中证指数官网", index_code, e)
            try:
                return self._fetch_index_daily_csi(index_code, start_date, end_date)
            except Exception:
                pass
            # 中证官网也失败 → 第三级：东方财富
            self._logger.info("中证指数官网对指数 %s 拉取失败，降级到东方财富源", index_code)
            try:
                return self._fetch_index_daily_em(index_code, start_date, end_date)
            except Exception:
                raise e

    # ------------------------------------------------------------------
    # 指数估值 PE/PB
    # ------------------------------------------------------------------

    def _fetch_index_valuation_csi(self, index_code: str) -> list[IndexValuation]:
        """通过中证指数官网日线数据提取 PE 估值。

        中证指数官网的 stock_zh_index_hist_csindex 在日线数据中附带
        "动态市盈率" 列，可用于所有中证系指数的估值计算。
        对历史 PE 值计算滚动百分位。

        Args:
            index_code: 指数代码

        Returns:
            按日期升序排列的估值数据列表；不支持或无数据时返回空列表
        """
        endpoint = "stock_zh_index_hist_csindex"
        self._log_request(endpoint, {"index_code": index_code, "purpose": "valuation"})
        start = time.perf_counter()
        try:
            yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
            df = ak.stock_zh_index_hist_csindex(
                symbol=index_code,
                start_date="20100101",
                end_date=yesterday,
            )
            if df is None or len(df) == 0:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return []

            # 中证指数官网 PE 列名可能为"滚动市盈率"或"动态市盈率"（依 AkShare 版本而异）
            pe_col = (
                "滚动市盈率"
                if "滚动市盈率" in df.columns
                else ("动态市盈率" if "动态市盈率" in df.columns else None)
            )
            if pe_col is None:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return []

            pe_values: list[tuple[date, float]] = []
            for _, row in df.iterrows():
                d = row["日期"]
                if isinstance(d, str):
                    d = datetime.strptime(d, "%Y-%m-%d").date()
                pe_val = row.get(pe_col)
                if pe_val is not None:
                    try:
                        pe_val_f = float(pe_val)
                        if pe_val_f > 0:
                            pe_values.append((d, pe_val_f))
                    except (ValueError, TypeError):
                        pass

            pe_values.sort(key=lambda x: x[0])

            # 计算历史百分位
            def _calc_percentile(data: list[tuple[date, float]]) -> dict[date, float]:
                result: dict[date, float] = {}
                for i, (d, val) in enumerate(data):
                    count_less = sum(1 for _, prev_val in data[:i] if prev_val < val)
                    percentile = round(count_less / i * 100, 2) if i > 0 else 50.0
                    result[d] = percentile
                return result

            pe_percentile_map = _calc_percentile(pe_values)

            results: list[IndexValuation] = []
            for d, pe in pe_values:
                results.append(
                    IndexValuation(
                        trade_date=d,
                        pe=pe,
                        pe_percentile=pe_percentile_map.get(d),
                        pb=None,
                        pb_percentile=None,
                        dividend_yield=None,
                    )
                )

            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(results), elapsed)
            return results
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            return []

    def fetch_index_valuation(self, index_code: str) -> list[IndexValuation]:
        """拉取指数 PE/PB 估值历史数据。

        二级降级策略：
        1. legulegu（乐股乐源）PE/PB 数据：覆盖 12 个主流指数，含 PE+PB+百分位
        2. 中证指数官网：覆盖所有中证指数，含 PE+百分位（无 PB）

        Args:
            index_code: 指数代码

        Returns:
            按日期升序排列的估值数据列表
        """
        name = _PE_PB_NAME_MAP.get(index_code)
        if name is None:
            self._logger.info(
                "%s 不在 legulegu 覆盖范围，降级到中证指数官网估值源",
                index_code,
            )
            return self._fetch_index_valuation_csi(index_code)

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
                    wait_seconds = 2**attempt
                    self._logger.info(
                        "指数 %s 估值拉取失败（第 %d 次），%d 秒后重试: %s",
                        index_code,
                        attempt + 1,
                        wait_seconds,
                        e,
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
