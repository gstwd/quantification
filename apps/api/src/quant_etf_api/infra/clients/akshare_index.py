from __future__ import annotations

import bisect
import math
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import akshare as ak

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus
from quant_etf_api.infra.clients.retry import with_retry

# 东方财富 API 调用超时上限（秒），防止代理/网络问题导致长时间阻塞
_EM_API_TIMEOUT = 5

# 东方财富通用指数接口超时上限（秒）：首次调用需拉取全市场指数→市场编号映射，耗时更长
_ZH_A_HIST_TIMEOUT = 15

# 增量拉取缓冲窗口（自然日）：起点回退该天数，保证边界 bar 的 prev_close/change_pct 可算
_INCREMENTAL_BUFFER_DAYS = 10

# 各源全量历史默认起始/结束哨兵（口径差异见各源注释，统一命名便于追踪）：
# - 腾讯: start/end 传空串，由上游自动返回最早/最新；
# - 中证: 20100101 起点（官网接口请求更早日期会额外返回 1990-01-01 基期伪行，故保持 2010）；
# - 东方财富（含通用接口）: 19700101 起点 + 20500101 终点哨兵，实际按指数上市时间返回。
_FULL_HISTORY_START_EM = "19700101"
_FULL_HISTORY_START_CSI = "20100101"
_FULL_HISTORY_END = "20500101"

# OHLC 完整性校验阈值：请求窗口内开盘/最高/最低缺失比例超过该值视为数据不合格，
# 触发降级到下一数据源（中证官网等官方源对部分历史区间仅提供收盘价，无 OHLC）。
# 回测调仓依赖开盘价，缺失会直接污染回测结果，故门槛设得较低。
_OHLC_MISSING_RATIO_LIMIT = 0.02

# 模块级共享线程池：超时保护的东财调用复用线程，避免每次调用新建线程池
_EM_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="akshare-em")


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
    source: str = "akshare"


def _calc_percentile(data: list[tuple[date, float]]) -> dict[date, float]:
    """按统一口径计算每个交易日的历史百分位（含当日）。

    百分位 = rank / (n - 1) * 100：
    - 样本为截至当日（含当日）的全部有效值，不使用未来数据（无前视偏差）；
    - rank 为 0-based 排名（≤ 当日值的数量 - 1，并列值计入自身），
      当日为历史最高时 = 100，最低时 = 0；
    - 首个交易日（n == 1）无历史可比，返回 50.0 作为中性占位。

    Args:
        data: 按日期升序排列的 (date, value) 列表。

    Returns:
        {date: 百分位} 映射，仅包含 data 中出现的日期。
    """
    sorted_values: list[float] = []
    result: dict[date, float] = {}
    for d, val in data:
        bisect.insort(sorted_values, val)
        n = len(sorted_values)
        if n <= 1:
            result[d] = 50.0
            continue
        rank = bisect.bisect_right(sorted_values, val) - 1
        result[d] = round(rank / (n - 1) * 100, 2)
    return result


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


def _parse_bar_date(value) -> date:
    """将上游日期的任意类型统一解析为 date。

    AkShare 各接口返回的日期列类型不一致（str / datetime / date / Timestamp），
    统一在此处收敛，避免每个源各自解析。

    Args:
        value: 上游日期单元格值。

    Returns:
        标准化后的 date。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


def _incremental_start_date(since_date: date) -> date:
    """计算增量拉取的缓冲起始日。

    以 since_date 直接作为起点会丢失起点 bar 的 prev_close/change_pct，
    因此回退 _INCREMENTAL_BUFFER_DAYS 个自然日（覆盖周末与长假断档）。

    Args:
        since_date: 增量基准日（不含当日）。

    Returns:
        应传给上游接口的起始日期。
    """
    return since_date - timedelta(days=_INCREMENTAL_BUFFER_DAYS)


def _build_index_bars(
    df,
    date_col: str,
    open_col: str,
    close_col: str,
    high_col: str,
    low_col: str,
    volume_col: str | None = None,
    amount_col: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[IndexDailyBar]:
    """将上游日线 DataFrame 统一转换为 IndexDailyBar 列表。

    集中处理各源差异：
    - 日期列类型与列名不同（腾讯/东财为英文列名，中证/通用为中文列名）；
    - 部分源缺少成交量或成交额列（腾讯无 volume、新浪无 amount），缺失列补 0；
    - 新浪等源不支持服务端日期过滤，统一在本地按 start_date/end_date 过滤。

    Args:
        df: 上游返回的日线 DataFrame。
        date_col: 日期列名。
        open_col/close_col/high_col/low_col: OHLC 列名。
        volume_col: 成交量列名，None 表示该源无成交量（补 0）。
        amount_col: 成交额列名，None 表示该源无成交额（补 0）。
        start_date: 起始日 'YYYYMMDD'，本地过滤下限（含），None 不过滤。
        end_date: 结束日 'YYYYMMDD'，本地过滤上限（含），None 不过滤。

    Returns:
        按日期升序排列的日线列表。
    """
    if df is None or df.empty:
        return []

    dates = [_parse_bar_date(v) for v in df[date_col]]
    opens = [float(v) for v in df[open_col]]
    closes = [float(v) for v in df[close_col]]
    highs = [float(v) for v in df[high_col]]
    lows = [float(v) for v in df[low_col]]
    volumes = (
        [float(v) if v is not None else 0.0 for v in df[volume_col]]
        if volume_col
        else [0.0] * len(df)
    )
    amounts = (
        [float(v) if v is not None else 0.0 for v in df[amount_col]]
        if amount_col
        else [0.0] * len(df)
    )

    start = datetime.strptime(start_date, "%Y%m%d").date() if start_date else None
    end = datetime.strptime(end_date, "%Y%m%d").date() if end_date else None

    bars: list[IndexDailyBar] = []
    for i, bar_date in enumerate(dates):
        if start is not None and bar_date < start:
            continue
        if end is not None and bar_date > end:
            continue
        bars.append(
            IndexDailyBar(
                trade_date=bar_date,
                open_price=opens[i],
                close_price=closes[i],
                high_price=highs[i],
                low_price=lows[i],
                volume=volumes[i],
                turnover=amounts[i],
            )
        )
    # 逐日计算涨跌幅（第一根无前收盘，保持 None）
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close_price
        if prev_close and prev_close != 0:
            bars[i].prev_close_price = prev_close
            bars[i].change_pct = round((bars[i].close_price - prev_close) / prev_close * 100, 4)
    return bars


def _is_valid_price(value: float | None) -> bool:
    """判断价格是否有效（非空、非 NaN、为正数）。

    Args:
        value: 价格或成交量数值。

    Returns:
        True 表示有效。
    """
    if value is None:
        return False
    try:
        if math.isnan(value):
            return False
    except TypeError:
        return False
    return value > 0


def _ohlc_missing_ratio(bars: list[IndexDailyBar]) -> float:
    """计算日线列表中 OHLC 缺失比例。

    开盘/最高/最低任一为空、NaN 或非正值即视为该日 OHLC 不完整。
    拉取时用于数据质量校验：请求窗口内缺失比例超过 _OHLC_MISSING_RATIO_LIMIT
    则放弃该源继续降级，避免"仅收盘价"的历史区间污染回测。

    Args:
        bars: 日线列表（已按请求窗口过滤）。

    Returns:
        缺失比例（0.0~1.0），空列表返回 0.0。
    """
    if not bars:
        return 0.0
    missing = sum(
        1
        for b in bars
        if not (
            _is_valid_price(b.open_price)
            and _is_valid_price(b.high_price)
            and _is_valid_price(b.low_price)
        )
    )
    return missing / len(bars)


_INDEX_NAME_CACHE: dict[str, str] | None = None
_EM_INDEX_NAME_CACHE: dict[str, str] | None = None


class AkShareIndexClient(BaseDataClient):
    """指数行情与估值客户端（基于 AkShare SDK）。

    封装三层数据源降级策略：
    - 日线：腾讯源 → 中证指数官网 → 东方财富 → 东方财富通用 → 新浪
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
        使用模块级共享线程池 _EM_EXECUTOR，避免每次调用新建线程；
        超时后仅放弃等待并取消 future（线程无法强制终止，但不再阻塞调用方）。

        Args:
            fn: 无参可调用对象
            timeout: 超时秒数

        Returns:
            函数返回值

        Raises:
            TimeoutError: 执行超时
        """
        future = _EM_EXECUTOR.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            future.cancel()
            raise

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

    @with_retry()
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
                start_date=start_date or _FULL_HISTORY_START_CSI,
                end_date=end_date or date.today().strftime("%Y%m%d"),
            )
            bars = _build_index_bars(
                df,
                date_col="日期",
                open_col="开盘",
                close_col="收盘",
                high_col="最高",
                low_col="最低",
                volume_col="成交量",
                amount_col="成交金额",
                start_date=start_date,
                end_date=end_date,
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

        ak.stock_zh_index_daily_em 要求 symbol 带市场前缀（sz/sh/csi/bj），
        传纯代码会直接返回空 DataFrame（历史缺陷：原实现传纯代码导致本源恒空），
        此处通过 index_code_id_map_em() 解析市场编号后组装前缀；
        解析失败时返回空列表，交由通用接口 index_zh_a_hist 兜底。
        调用受 _EM_API_TIMEOUT 超时保护；不叠加 with_retry，因为超时后线程仍可能
        在运行，重试会叠加并发请求，降级链本身已提供容错。

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
        symbol = self._em_market_symbol(index_code)
        if symbol is None:
            # 无法解析市场编号时跳过本源，交由通用接口 index_zh_a_hist 兜底
            self._logger.info("指数 %s 无法解析东方财富市场编号，跳过 %s", index_code, endpoint)
            return []
        try:
            df = self._call_with_timeout(
                lambda: ak.stock_zh_index_daily_em(
                    symbol=symbol,
                    start_date=start_date or _FULL_HISTORY_START_EM,
                    end_date=end_date or _FULL_HISTORY_END,
                )
            )
            bars = _build_index_bars(
                df,
                date_col="date",
                open_col="open",
                close_col="close",
                high_col="high",
                low_col="low",
                volume_col="volume",
                amount_col="amount",
                start_date=start_date,
                end_date=end_date,
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

    @staticmethod
    def _em_market_symbol(index_code: str) -> str | None:
        """将 index_code 解析为东方财富 stock_zh_index_daily_em 所需的市场前缀代码。

        东方财富 kline 接口要求 symbol 带市场前缀（sz/sh/csi/bj），
        市场编号通过 akshare 的 index_code_id_map_em() 映射获取（进程级缓存）。

        Args:
            index_code: 指数代码，如 '000300'。

        Returns:
            带市场前缀的 symbol，如 'sh000300'；无法解析时返回 None。
        """
        try:
            market_map = ak.index_code_id_map_em()
        except Exception:
            return None
        market_id = market_map.get(index_code)
        if market_id is None:
            return None
        prefix = {0: "sz", 1: "sh", 2: "csi"}.get(int(market_id))
        if prefix is None:
            return None
        return f"{prefix}{index_code}"

    @with_retry()
    def _fetch_index_daily_tx(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过腾讯源拉取指数日线（主源）。

        ak.stock_zh_index_daily_tx 支持服务端日期过滤，但无成交量列
        （仅 date/OHLC/amount），且全量历史按年分页请求，拉全量较慢；
        限定期望日期范围时单请求即可返回。

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
            bars = _build_index_bars(
                df,
                date_col="date",
                open_col="open",
                close_col="close",
                high_col="high",
                low_col="low",
                amount_col="amount",
                start_date=start_date,
                end_date=end_date,
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(bars), elapsed)
            return bars
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def _fetch_index_daily_zh_a_hist(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过东方财富通用接口 index_zh_a_hist 拉取指数日线（东财第二源）。

        通用接口接受纯指数代码，内部通过指数→市场编号映射自动解析交易所归属，
        覆盖东财全市场指数（含深证 399xxx 与中证系），返回列含涨跌幅/换手率。
        注意：接口内部始终拉取全量再按日期切片，且首次调用需拉取全市场映射，
        故超时上限放宽到 _ZH_A_HIST_TIMEOUT。

        Args:
            index_code: 指数代码，如 399001
            start_date: 起始日 'YYYYMMDD'，None 表示 19700101
            end_date: 结束日 'YYYYMMDD'，None 表示 22220101

        Returns:
            按日期升序排列的日线数据列表
        """
        endpoint = "index_zh_a_hist"
        self._log_request(endpoint, {"index_code": index_code})
        start = time.perf_counter()
        try:
            df = self._call_with_timeout(
                lambda: ak.index_zh_a_hist(
                    symbol=index_code,
                    period="daily",
                    start_date=start_date or _FULL_HISTORY_START_EM,
                    end_date=end_date or _FULL_HISTORY_END,
                ),
                timeout=_ZH_A_HIST_TIMEOUT,
            )
            bars = _build_index_bars(
                df,
                date_col="日期",
                open_col="开盘",
                close_col="收盘",
                high_col="最高",
                low_col="最低",
                volume_col="成交量",
                amount_col="成交额",
                start_date=start_date,
                end_date=end_date,
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(bars), elapsed)
            return bars
        except FutureTimeoutError:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(
                endpoint,
                TimeoutError(f"东方财富通用接口超时（{_ZH_A_HIST_TIMEOUT}s）"),
                elapsed,
            )
            raise
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    @with_retry()
    def _fetch_index_daily_sina(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过新浪源拉取指数日线（最后兜底）。

        ak.stock_zh_index_daily 无日期参数，始终返回全量历史（本地过滤）；
        深证指数（399xxx）覆盖好且全量拉取快（约 0.5s），但无成交额列（补 0），
        且上游对大量抓取有封 IP 风险，仅作为其他源全部失败时的兜底。

        Args:
            index_code: 指数代码，如 399001
            start_date: 起始日 'YYYYMMDD'，本地过滤
            end_date: 结束日 'YYYYMMDD'，本地过滤

        Returns:
            按日期升序排列的日线数据列表
        """
        endpoint = "stock_zh_index_daily"
        symbol = _index_code_to_ak_symbol(index_code)
        self._log_request(endpoint, {"index_code": index_code, "symbol": symbol})
        start = time.perf_counter()
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
            bars = _build_index_bars(
                df,
                date_col="date",
                open_col="open",
                close_col="close",
                high_col="high",
                low_col="low",
                volume_col="volume",
                start_date=start_date,
                end_date=end_date,
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(bars), elapsed)
            return bars
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

        五级降级策略（按覆盖范围与数据完整度排序）：
        1. 腾讯源 stock_zh_index_daily_tx（主源，支持服务端日期过滤）
        2. 中证指数官网 stock_zh_index_hist_csindex（中证系全覆盖，含 930/931/932 自定义指数）
        3. 东方财富 stock_zh_index_daily_em（带市场前缀，含成交量/成交额，5 秒超时保护）
        4. 东方财富通用接口 index_zh_a_hist（纯代码自动解析市场，覆盖最全）
        5. 新浪 stock_zh_index_daily（最后兜底，无成交额、全量拉取、有封 IP 风险）

        与历史行为差异：
        - 任一源返回空数据时继续降级（此前中证源空数据会直接结束）；
        - 每个源返回后先做 OHLC 完整性校验：请求窗口内开盘/最高/最低缺失比例超过
          _OHLC_MISSING_RATIO_LIMIT 视为数据不合格，继续降级到下一源
          （如中证官网对红利低波等指数 2016-2018 年大部分交易日仅返回收盘价）；
        - 若所有源均不完整，回退首个有数据的源并告警（尽量保证有数据可用）；
        - 全部源失败时抛出最后一个源的异常（此前抛腾讯源原始异常）。

        Args:
            index_code: 指数代码，如 000300
            start_date: 起始日 'YYYYMMDD'，None 表示最早
            end_date: 结束日 'YYYYMMDD'，None 表示最新

        Returns:
            按日期升序排列的日线数据列表
        """
        sources = [
            ("腾讯源", self._fetch_index_daily_tx),
            ("中证指数官网", self._fetch_index_daily_csi),
            ("东方财富", self._fetch_index_daily_em),
            ("东方财富通用", self._fetch_index_daily_zh_a_hist),
            ("新浪", self._fetch_index_daily_sina),
        ]
        last_error: Exception | None = None
        fallback_bars: list[IndexDailyBar] | None = None
        fallback_source: str | None = None
        for source_name, fetcher in sources:
            try:
                bars = fetcher(index_code, start_date, end_date)
            except Exception as e:
                last_error = e
                self._logger.info(
                    "指数 %s 日线源 %s 拉取失败，降级到下一源: %s",
                    index_code,
                    source_name,
                    e,
                )
                continue
            if not bars:
                self._logger.info(
                    "指数 %s 日线源 %s 返回空数据，降级到下一源", index_code, source_name
                )
                continue
            missing_ratio = _ohlc_missing_ratio(bars)
            if missing_ratio > _OHLC_MISSING_RATIO_LIMIT:
                # 数据不完整（如官方源部分历史区间仅收盘价）：记下作为兜底并继续降级
                if fallback_bars is None:
                    fallback_bars = bars
                    fallback_source = source_name
                self._logger.warning(
                    "指数 %s 日线源 %s OHLC 缺失比例 %.2f%% 超过阈值 %.2f%%，继续降级",
                    index_code,
                    source_name,
                    missing_ratio * 100,
                    _OHLC_MISSING_RATIO_LIMIT * 100,
                )
                continue
            self._logger.info(
                "指数 %s 日线最终由 %s 提供，共 %d 条",
                index_code,
                source_name,
                len(bars),
            )
            return bars
        if fallback_bars is not None:
            self._logger.warning(
                "指数 %s 所有日线源 OHLC 均不完整，回退到 %s 的部分数据（%d 条）",
                index_code,
                fallback_source,
                len(fallback_bars),
            )
            return fallback_bars
        if last_error is not None:
            raise last_error
        return []

    def fetch_index_daily_since(self, index_code: str, since_date: date) -> list[IndexDailyBar]:
        """增量拉取指数日线：仅拉取 since_date 之前的缓冲窗口到最新。

        直接以 since_date 为起点会导致起点 bar 的 prev_close/change_pct 缺失，
        因此向前回退 _INCREMENTAL_BUFFER_DAYS 个自然日（覆盖节假日/周末断档），
        调用方按自身口径过滤（如 trade_date > since_date）即可丢弃缓冲行。

        Args:
            index_code: 指数代码，如 000300
            since_date: 起始日期（不含，即仅拉取该日之后的数据）

        Returns:
            按日期升序排列的日线数据列表（含缓冲窗口内的历史行）
        """
        start = _incremental_start_date(since_date)
        return self.fetch_index_daily(index_code, start_date=start.strftime("%Y%m%d"))

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
                        source="csindex",
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
                    source="legulegu",
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
            # 只拉最近一周，避免健康检查触发全量历史拉取（腾讯全量按年分页较慢）
            start_date = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
            bars = self.fetch_index_daily("000001", start_date=start_date)
            elapsed = (time.perf_counter() - start) * 1000
            ok = len(bars) > 0
            return HealthStatus(
                healthy=ok,
                message="AkShare 指数接口可达" if ok else "AkShare 指数接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
