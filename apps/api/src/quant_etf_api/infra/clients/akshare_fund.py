from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, TypeVar

import akshare as ak

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus

_T = TypeVar("_T")


def _retry(
    fn: Callable[..., _T], *args: Any, attempts: int = 3, delay: float = 0.5, **kwargs: Any
) -> _T:
    """最多重试 attempts 次，每次间隔递增 delay 秒，最后一次失败时抛出原异常。

    用于应对 AkShare 上游的代理断连、ConnectionReset 等瞬时网络错误。
    """
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


@dataclass
class AkShareEtfInfo:
    """AkShare 获取的 ETF 基金基本信息。"""

    code: str
    name_cn: str
    fund_full_name: str | None
    fund_company: str | None
    fund_type: str | None
    establishment_date: str | None
    tracking_index_name: str | None


@dataclass
class AkShareEtfDailyBar:
    """AkShare 获取的 ETF 日线行情数据（前复权）。"""

    trade_date: date
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float
    turnover: float
    change_pct: float | None
    amplitude: float | None


@dataclass
class AkShareEtfShareSnapshot:
    """AkShare 获取的 ETF 份额快照（当日行情）。"""

    code: str
    price: float  # 单位：元/份
    shares_total: float  # 单位：亿份
    aum: float  # 单位：亿元


# 基金类型 → category 映射
_FUND_TYPE_CATEGORY_MAP: dict[str, str] = {
    "指数型-股票": "broad_index",
    "ETF-场内": "broad_index",
    "联接基金": "broad_index",
    "QDII-ETF": "cross_border",
    "QDII": "cross_border",
    "商品（不含QDII）": "commodity",
    "债券型-长债": "bond",
    "债券型-中短债": "bond",
    "货币型": "money_market",
}

# fund_etf_spot_em 全量行情缓存，10 分钟内重用避免批量摄取时重复调用
_etf_spot_cache_df = None
_etf_spot_cache_ts: float = 0.0
_ETF_SPOT_CACHE_TTL = 600.0


def _parse_establishment_date(raw: str | None) -> str | None:
    """从 '2012年05月04日 / 329.686亿份' 格式中提取日期。

    Args:
        raw: 成立日期/规模原始文本

    Returns:
        'YYYY-MM-DD' 格式的日期字符串，解析失败时返回 None
    """
    if not raw:
        return None
    m = re.search(r"(\d{4})年(\d{2})月(\d{2})日", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def map_fund_type_to_category(fund_type: str | None) -> str:
    """将基金类型字符串映射为内部 category 枚举值。

    Args:
        fund_type: 基金类型，如 '指数型-股票'

    Returns:
        category 值，无法识别时返回 'other'
    """
    if not fund_type:
        return "other"
    if fund_type in _FUND_TYPE_CATEGORY_MAP:
        return _FUND_TYPE_CATEGORY_MAP[fund_type]
    if "指数" in fund_type:
        return "broad_index"
    if "债券" in fund_type:
        return "bond"
    if "货币" in fund_type:
        return "money_market"
    return "other"


class AkShareFundClient(BaseDataClient):
    """ETF 基金数据客户端（基于 AkShare SDK）。

    封装以下三类接口：
    1. ETF 基金档案（fund_overview_em）
    2. ETF 日线 OHLCV（fund_etf_hist_em，前复权）
    3. ETF 份额快照（fund_etf_spot_em，进程内缓存 10 分钟）
    """

    source_name = "akshare_fund"

    def fetch_etf_info(self, code: str) -> AkShareEtfInfo | None:
        """获取 ETF 基金基本信息。

        通过 akshare fund_overview_em 接口从东方财富基金档案页抓取基本概况。

        Args:
            code: ETF 代码，如 '510300'

        Returns:
            AkShareEtfInfo 数据，获取失败时返回 None
        """
        endpoint = "fund_overview_em"
        self._log_request(endpoint, {"code": code})
        start = time.perf_counter()
        try:
            df = _retry(ak.fund_overview_em, symbol=code)
            if df is None or df.empty:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return None

            def _col(name: str) -> str | None:
                """安全读取 DataFrame 列值。"""
                if name in df.columns:
                    val = df[name].iloc[0]
                    if val and str(val).strip() and str(val).strip() != "---":
                        return str(val).strip()
                return None

            name_cn = _col("基金简称") or code
            result = AkShareEtfInfo(
                code=code,
                name_cn=name_cn,
                fund_full_name=_col("基金全称"),
                fund_company=_col("基金管理人"),
                fund_type=_col("基金类型"),
                establishment_date=_parse_establishment_date(_col("成立日期/规模")),
                tracking_index_name=_col("跟踪标的") or _col("业绩比较基准"),
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, 1, elapsed)
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    @staticmethod
    def _exchange_prefix(code: str) -> str:
        """根据 ETF 代码推断交易所前缀（新浪/腾讯接口需要）。

        规则：以 '5' 开头 → 上海（sh）；以 '1' 开头 → 深圳（sz）。
        """
        return "sh" if code.startswith("5") else "sz"

    def fetch_etf_daily_bars(
        self,
        code: str,
        start_date: str = "19900101",
        end_date: str = "",
    ) -> list[AkShareEtfDailyBar]:
        """拉取 ETF 日线 OHLCV 数据（前复权）。

        使用新浪后端（fund_etf_hist_sina），一次返回全量历史数据，
        再在客户端按 start_date/end_date 过滤，避免东方财富代理封锁问题。

        Args:
            code: ETF 代码，如 '510300'
            start_date: 起始日 'YYYYMMDD'，含；早于上市日则从上市日起
            end_date: 结束日 'YYYYMMDD'，含；默认当日

        Returns:
            按日期升序排列的日线数据列表
        """
        endpoint = "fund_etf_hist_sina"
        if not end_date:
            end_date = date.today().strftime("%Y%m%d")
        self._log_request(endpoint, {"code": code, "start_date": start_date, "end_date": end_date})
        start = time.perf_counter()
        try:
            symbol = self._exchange_prefix(code) + code
            df = _retry(ak.fund_etf_hist_sina, symbol=symbol)
            if df is None or df.empty:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return []

            # 客户端过滤日期范围
            start_d = datetime.strptime(start_date, "%Y%m%d").date()
            end_d = datetime.strptime(end_date, "%Y%m%d").date()
            bars: list[AkShareEtfDailyBar] = []
            for _, row in df.iterrows():
                bar_date = row["date"]
                if isinstance(bar_date, str):
                    bar_date = datetime.strptime(bar_date, "%Y-%m-%d").date()
                if bar_date < start_d or bar_date > end_d:
                    continue
                bars.append(
                    AkShareEtfDailyBar(
                        trade_date=bar_date,
                        open_price=float(row["open"]),
                        close_price=float(row["close"]),
                        high_price=float(row["high"]),
                        low_price=float(row["low"]),
                        volume=float(row["volume"]),
                        turnover=float(row["amount"]),
                        change_pct=None,
                        amplitude=None,
                    )
                )
            # 逐日计算涨跌幅和振幅（第一根无前收盘，保持 None）
            for i in range(1, len(bars)):
                prev_close = bars[i - 1].close_price
                if prev_close and prev_close != 0:
                    bars[i].change_pct = round(
                        (bars[i].close_price - prev_close) / prev_close * 100, 4
                    )
                    bars[i].amplitude = round(
                        (bars[i].high_price - bars[i].low_price) / prev_close * 100, 4
                    )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(bars), elapsed)
            return bars
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def fetch_share_snapshot(self, code: str) -> AkShareEtfShareSnapshot | None:
        """拉取 ETF 份额快照（含价格、总份额、AUM）。

        调用 fund_etf_spot_em 获取全量 ETF 行情，结果在进程内缓存 10 分钟，
        避免同一摄取批次中对同一接口重复请求。
        若总份额列不存在，则由 AUM / 价格 推算。

        Args:
            code: ETF 代码，如 '510300'

        Returns:
            AkShareEtfShareSnapshot，未找到或价格异常时返回 None
        """
        global _etf_spot_cache_df, _etf_spot_cache_ts
        endpoint = "fund_etf_spot_em"
        self._log_request(endpoint, {"code": code})
        start = time.perf_counter()
        try:
            now = time.time()
            if _etf_spot_cache_df is None or (now - _etf_spot_cache_ts) > _ETF_SPOT_CACHE_TTL:
                _etf_spot_cache_df = _retry(ak.fund_etf_spot_em)
                _etf_spot_cache_ts = now

            df = _etf_spot_cache_df
            if df is None or df.empty:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return None

            # 基金代码列名因版本不同可能有差异
            code_col = next((c for c in ("基金代码", "代码") if c in df.columns), None)
            if code_col is None:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return None

            row_df = df[df[code_col] == code]
            if row_df.empty:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return None

            row = row_df.iloc[0]

            price_col = next((c for c in ("最新价", "收盘价") if c in df.columns), None)
            price = float(row[price_col] or 0) if price_col else 0.0
            if price <= 0:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return None

            # 读取总份额：列名 '最新份额'，单位为份，转换为亿份
            shares_total: float = 0.0
            if "最新份额" in df.columns:
                val = row.get("最新份额")
                if val is not None and float(val or 0) > 0:
                    shares_total = round(float(val) / 1e8, 2)

            # 读取 AUM：列名 '总市值'，单位为元，转换为亿元
            aum: float = 0.0
            for col in ("总市值", "流通市值"):
                if col in df.columns:
                    val = row.get(col)
                    if val is not None and str(val).strip() not in ("", "--", "---"):
                        try:
                            aum = round(float(val) / 1e8, 2)
                        except (ValueError, TypeError):
                            pass
                        break

            if shares_total <= 0 and aum > 0 and price > 0:
                shares_total = round(aum / price, 2)

            result = AkShareEtfShareSnapshot(
                code=code,
                price=round(price, 3),
                shares_total=shares_total,
                aum=aum,
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, 1, elapsed)
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def health_check(self) -> HealthStatus:
        """通过查询 510300 日线和份额检测连通性。"""
        try:
            start = time.perf_counter()
            bars = self.fetch_etf_daily_bars(
                "510300",
                start_date=date.today().strftime("%Y%m%d"),
            )
            # 交易日当天可能还未收盘，退一步取最近 5 日
            if not bars:
                from datetime import timedelta

                s = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
                bars = self.fetch_etf_daily_bars("510300", start_date=s)
            elapsed = (time.perf_counter() - start) * 1000
            ok = len(bars) > 0
            return HealthStatus(
                healthy=ok,
                message="AkShare ETF 接口可达" if ok else "AkShare ETF 接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
