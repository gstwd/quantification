"""Tushare Pro 市场数据客户端集合（估值 / 宏观 / 成分 / 个股收盘与元数据）。

所有客户端共用同一套 Token 与节流约定：
- Token 从 settings（QUANT_ETF_TUSHARE_TOKEN）读取，未配置时 is_configured()=False；
- 调用前按 _TUSHARE_MIN_INTERVAL 节流，避免触发 2000 积分档位的接口频次限制；
- 只负责拉取与字段归一化，不直接写库；落库前的多源优先级编排在服务层完成。
"""

from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import date, datetime, timedelta
from typing import Any

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.clients.akshare_index import IndexValuation, _calc_percentile
from quant_etf_api.infra.clients.akshare_macro import MacroIndicator
from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus

logger = logging.getLogger(__name__)

# 通用调用最小间隔（秒），2000 积分多数接口 200 次/分钟，留出余量
_TUSHARE_MIN_INTERVAL = float(os.getenv("TUSHARE_MARKET_MIN_INTERVAL", "0.4"))

# 单次 Tushare 请求超时（秒）：SDK 底层未设置超时，防止偶发连接挂起
# 拖死整个回填任务
_TUSHARE_CALL_TIMEOUT = float(os.getenv("TUSHARE_MARKET_TIMEOUT", "25"))

# 共享线程池：超时保护复用少量线程，避免每次调用新建
_TUSHARE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="tushare-call",
)

# 指数估值起始年：Tushare index_dailybasic 自 2006 年前后开始提供
_VALUATION_START_YEAR = 2006

# 申万成分单次请求上限（单次最大 5000 行），成分按 L1 行业分批拉取
_INDEX_MEMBER_PAGE_ROWS = 5000

# 个股接口字段清单：显式声明避免 Tushare 默认列裁剪导致扩展字段缺失
_STOCK_BASIC_FIELDS = "ts_code,symbol,name,market,exchange,list_status,list_date,delist_date"
_STOCK_DAILY_FIELDS = (
    "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount,ah_vol,ah_amount"
)
_STOCK_ADJ_FACTOR_FIELDS = "ts_code,trade_date,adj_factor"
_STOCK_DAILY_BASIC_FIELDS = (
    "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,"
    "ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv,"
    "limit_status"
)
_STOCK_MONEYFLOW_FIELDS = (
    "ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,"
    "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,"
    "sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,"
    "sell_elg_amount,net_mf_vol,net_mf_amount"
)

# 估值全量历史按“北京日期”做进程级缓存：当日多次补拉（日频摄取/手动刷新/
# 数据管理操作）只触发一次全量分页，避免重复消耗接口频次
_VALUATION_DAILY_CACHE: dict[str, tuple[str, list[IndexValuation]]] = {}


def _stock_ts_suffix(stock_code: str) -> str | None:
    """根据 A 股代码推断 Tushare 交易所后缀（SH/SZ/BJ）。

    Args:
        stock_code: 6 位股票代码，如 600000。

    Returns:
        大写的交易所后缀；北交所 43/83/87/88/92/920 开头归 BJ，未知返回 None。
    """
    if stock_code.startswith(("60", "68", "90")):
        return "SH"
    if stock_code.startswith(("00", "30", "12", "15", "16", "18")):
        return "SZ"
    if stock_code.startswith(("43", "83", "87", "88", "92")):
        return "BJ"
    return None


def _parse_yyyymmdd(value: Any) -> date | None:
    """把 Tushare 的 'YYYYMMDD' 或 'YYYY-MM-DD' 日期解析为 date。

    Args:
        value: 上游日期值（str/date/datetime/NaN）。

    Returns:
        date；无法解析时返回 None。
    """
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class _TushareBaseMixin:
    """Tushare Pro API 客户端公共行为（延迟建链 + 实例级节流）。"""

    def __init__(self, token: str | None = None) -> None:
        """初始化公共字段。

        Args:
            token: Tushare Token；未传入时读取 settings。
        """
        super().__init__()
        # None 表示读取配置；显式传空串则视为“未配置”，便于测试与禁用
        self._token = token if token is not None else get_settings().tushare_token
        self._pro: Any = None
        self._last_call_ts = 0.0

    def is_configured(self) -> bool:
        """判断是否已配置 Tushare Token。"""
        return bool(self._token)

    def _get_pro(self) -> Any:
        """延迟创建 Tushare Pro API 实例（仅首次调用时初始化）。"""
        if self._pro is None:
            if not self._token:
                raise RuntimeError("未配置 TUSHARE_TOKEN，tushare 数据源不可用")
            import tushare as ts

            ts.set_token(self._token)
            self._pro = ts.pro_api()
        return self._pro

    def _throttle(self) -> None:
        """按最小调用间隔节流，避免触发 Tushare 频次限制。"""
        elapsed = time.monotonic() - self._last_call_ts
        if elapsed < _TUSHARE_MIN_INTERVAL:
            time.sleep(_TUSHARE_MIN_INTERVAL - elapsed)
        self._last_call_ts = time.monotonic()

    def _fetch(self, endpoint: str, **params: Any):
        """调用 Tushare Pro 接口并统一记录请求/响应/错误。

        Args:
            endpoint: 接口名，如 index_dailybasic。
            **params: 接口参数。

        Returns:
            Tushare 返回的 DataFrame；接口无数据时为空 DataFrame。

        Raises:
            原样上抛 Tushare 的异常（由服务层决定降级）。
        """
        self._log_request(endpoint, params)
        start = time.perf_counter()
        try:
            pro = self._get_pro()
            self._throttle()
            future = _TUSHARE_EXECUTOR.submit(lambda: getattr(pro, endpoint)(**params))
            try:
                df = future.result(timeout=_TUSHARE_CALL_TIMEOUT)
            except FutureTimeoutError:
                future.cancel()
                raise TimeoutError(f"tushare {endpoint} 调用超过 {_TUSHARE_CALL_TIMEOUT}s")
            elapsed = (time.perf_counter() - start) * 1000
            rows = 0 if df is None or df.empty else len(df)
            self._log_response(endpoint, rows, elapsed)
            return df
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, exc, elapsed)
            raise


class TushareIndexValuationClient(_TushareBaseMixin, BaseDataClient):
    """Tushare Pro 指数估值客户端（index_dailybasic）。

    覆盖范围：Tushare 指数估值仅支持固定 12 个系列（沪深300/上证50/中证500/
    深证成指/创业板指等），本系统基准指数中只有 000300/000016/000905 在列，
    其余指数继续由 AkShare（乐咕乐股/中证官网）负责。

    兼容口径：
    - pe 取 pe_ttm（与既有 legulegu 序列的平均差异更小），pb 直接使用；
    - 百分位沿用 index_valuation 统一的 rank/(n-1)*100 口径，按拉取到的
      全量历史逐日滚动计算，保证新日期可与历史日期比较；
    - source 标识为 "tushare"，与 legulegu/csindex 行区分。
    """

    source_name = "tushare_valuation"

    # 基准指数代码 → Tushare ts_code（仅包含 index_dailybasic 明确覆盖的指数）
    SUPPORTED_INDEX_CODES: dict[str, str] = {
        "000300": "000300.SH",
        "000016": "000016.SH",
        "000905": "000905.SH",
    }

    @classmethod
    def clear_cache(cls) -> None:
        """清空估值当日缓存（测试与手动刷新使用）。"""
        _VALUATION_DAILY_CACHE.clear()

    def supports(self, index_code: str) -> bool:
        """判断指数是否在 Tushare 指数估值覆盖范围。

        Args:
            index_code: 指数代码，如 000300。

        Returns:
            True 表示支持。
        """
        return index_code in self.SUPPORTED_INDEX_CODES

    def fetch_index_valuation(self, index_code: str) -> list[IndexValuation]:
        """拉取指数 PE/PB 估值全量历史并计算滚动百分位。

        Args:
            index_code: 指数代码，如 000300；不在覆盖范围时返回空列表。

        Returns:
            按日期升序排列的 IndexValuation 列表，source="tushare"。
        """
        ts_code = self.SUPPORTED_INDEX_CODES.get(index_code)
        if ts_code is None or not self.is_configured():
            return []
        cache_key = f"{self._token}:{ts_code}"
        cache_date, cached = _VALUATION_DAILY_CACHE.get(cache_key, (None, []))
        today_key = today_cn().isoformat()
        if cache_date == today_key and cached:
            return list(cached)

        rows: list[tuple[date, float | None, float | None]] = []
        current_year = today_cn().year
        for year in range(_VALUATION_START_YEAR, current_year + 1):
            start_date = f"{year}0101"
            end_date = f"{year}1231"
            df = self._fetch(
                "index_dailybasic",
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields="trade_date,pe_ttm,pb",
            )
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                trade_date = _parse_yyyymmdd(row.get("trade_date"))
                if trade_date is None:
                    continue
                pe = _to_float(row.get("pe_ttm"))
                pb = _to_float(row.get("pb"))
                rows.append((trade_date, pe, pb))

        rows.sort(key=lambda item: item[0])
        pe_series: list[tuple[date, float]] = [
            (d, v) for d, v, _ in rows if v is not None and v > 0
        ]
        pb_series: list[tuple[date, float]] = [
            (d, v) for d, _, v in rows if v is not None and v > 0
        ]
        pe_percentile_map = _calc_percentile(pe_series)
        pb_percentile_map = _calc_percentile(pb_series)
        values: list[IndexValuation] = []
        for trade_date, pe, pb in rows:
            values.append(
                IndexValuation(
                    trade_date=trade_date,
                    pe=pe,
                    pe_percentile=pe_percentile_map.get(trade_date),
                    pb=pb,
                    pb_percentile=pb_percentile_map.get(trade_date),
                    dividend_yield=None,
                    source="tushare",
                )
            )
        _VALUATION_DAILY_CACHE[cache_key] = (today_key, values)
        return list(values)

    def health_check(self) -> HealthStatus:
        """通过拉取沪深300近期估值检测连通性。"""
        if not self.is_configured():
            return HealthStatus(healthy=False, message="未配置 TUSHARE_TOKEN")
        try:
            start = time.perf_counter()
            df = self._fetch(
                "index_dailybasic",
                ts_code="000300.SH",
                start_date=(today_cn() - timedelta(days=15)).strftime("%Y%m%d"),
                end_date=today_cn().strftime("%Y%m%d"),
                fields="trade_date,pe_ttm,pb",
            )
            elapsed = (time.perf_counter() - start) * 1000
            ok = df is not None and not df.empty
            return HealthStatus(
                healthy=ok,
                message="tushare 估值接口可达" if ok else "tushare 估值接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as exc:
            return HealthStatus(healthy=False, message=str(exc))


class TushareMacroClient(_TushareBaseMixin, BaseDataClient):
    """Tushare Pro 宏观指标客户端（cn_cpi / cn_pmi / shibor_lpr）。

    与 AkShareMacroClient 返回相同的 MacroIndicator 数据结构，便于服务层按
    数据组做“Tushare 优先、AkShare 兜底”的切换。LPR 接口（shibor_lpr）在
    2000 积分档位约 1 次/小时，调用失败时由服务层自动回退 AkShare。
    """

    source_name = "tushare_macro"

    def fetch_cpi_monthly(self) -> list[MacroIndicator]:
        """拉取中国月度 CPI 同比（全国口径 nt_yoy）。

        Returns:
            MacroIndicator 列表，period 为 'YYYY-MM'，period_date 为当月首日。
        """
        current = today_cn().strftime("%Y%m")
        df = self._fetch("cn_cpi", start_m="201001", end_m=current)
        results: list[MacroIndicator] = []
        if df is None or df.empty:
            return results
        for _, row in df.iterrows():
            month = str(row.get("month") or "").strip()
            if not re.fullmatch(r"\d{6}", month):
                continue
            value = _to_float(row.get("nt_yoy"))
            if value is None:
                continue
            period = f"{month[:4]}-{month[4:]}"
            results.append(
                MacroIndicator(
                    indicator_code="cpi",
                    indicator_name="居民消费价格指数(CPI)同比",
                    period=period,
                    value=value,
                    unit="%",
                    period_date=f"{period}-01",
                    source="tushare",
                )
            )
        return results

    def fetch_pmi(self) -> list[MacroIndicator]:
        """拉取中国制造业 PMI 月度数据（PMI010000 列）。

        Returns:
            MacroIndicator 列表，period 为 'YYYY-MM'。
        """
        current = today_cn().strftime("%Y%m")
        df = self._fetch(
            "cn_pmi",
            start_m="201001",
            end_m=current,
            fields="MONTH,PMI010000",
        )
        results: list[MacroIndicator] = []
        if df is None or df.empty:
            return results
        for _, row in df.iterrows():
            month = str(row.get("MONTH") or "").strip()
            if not re.fullmatch(r"\d{6}", month):
                continue
            value = _to_float(row.get("PMI010000"))
            if value is None:
                continue
            period = f"{month[:4]}-{month[4:]}"
            results.append(
                MacroIndicator(
                    indicator_code="pmi",
                    indicator_name="制造业采购经理指数(PMI)",
                    period=period,
                    value=value,
                    unit="%",
                    period_date=f"{period}-01",
                    source="tushare",
                )
            )
        return results

    @staticmethod
    def _lpr_columns(df: Any) -> tuple[str, str | None, str | None]:
        """从 shibor_lpr 返回列中定位日期/1年/5年列（列名随版本变化）。

        Args:
            df: shibor_lpr 返回的 DataFrame。

        Returns:
            (date_col, one_year_col, five_year_col)，找不到的列为 None。
        """
        columns = list(df.columns)
        date_col = next(
            (c for c in ("date", "trade_date") if c in columns),
            None,
        )
        one_col = next(
            (c for c in ("1y", "lpr1y", "LPR1Y", "lpr_1y", "in_1y") if c in columns),
            None,
        )
        five_col = next(
            (c for c in ("5y", "lpr5y", "LPR5Y", "lpr_5y", "in_5y") if c in columns),
            None,
        )
        return date_col or "", one_col, five_col

    def fetch_lpr(self) -> list[MacroIndicator]:
        """拉取 LPR 报价（1 年期与 5 年期）。

        注意：该接口在 2000 积分档位约 1 次/小时，服务层应在失败或空数据时
        回退 AkShare，不要高频调用。

        Returns:
            MacroIndicator 列表，每条报价日输出 lpr1y 与 lpr5y 两条。
        """
        end = today_cn().strftime("%Y%m%d")
        df = self._fetch("shibor_lpr", start_date="20130101", end_date=end)
        results: list[MacroIndicator] = []
        if df is None or df.empty:
            return results
        date_col, one_col, five_col = self._lpr_columns(df)
        if not date_col:
            return results
        for _, row in df.iterrows():
            trade_date = _parse_yyyymmdd(row.get(date_col))
            if trade_date is None:
                continue
            period = trade_date.isoformat()
            if one_col is not None:
                value = _to_float(row.get(one_col))
                if value is not None:
                    results.append(
                        MacroIndicator(
                            indicator_code="lpr1y",
                            indicator_name="贷款市场报价利率(LPR) 1年期",
                            period=period,
                            value=value,
                            unit="%",
                            period_date=period,
                            source="tushare",
                        )
                    )
            if five_col is not None:
                value = _to_float(row.get(five_col))
                if value is not None:
                    results.append(
                        MacroIndicator(
                            indicator_code="lpr5y",
                            indicator_name="贷款市场报价利率(LPR) 5年期",
                            period=period,
                            value=value,
                            unit="%",
                            period_date=period,
                            source="tushare",
                        )
                    )
        return results

    def health_check(self) -> HealthStatus:
        """通过拉取最近 PMI 检测连通性。"""
        if not self.is_configured():
            return HealthStatus(healthy=False, message="未配置 TUSHARE_TOKEN")
        try:
            start = time.perf_counter()
            rows = self.fetch_pmi()
            elapsed = (time.perf_counter() - start) * 1000
            ok = len(rows) > 0
            return HealthStatus(
                healthy=ok,
                message="tushare 宏观接口可达" if ok else "tushare 宏观接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as exc:
            return HealthStatus(healthy=False, message=str(exc))


class TushareIndexMemberClient(_TushareBaseMixin, BaseDataClient):
    """Tushare Pro 指数成分客户端（index_weight）。

    index_weight 按自然月发布指数成分与权重（月度快照，约月末更新），
    覆盖沪深300/中证500/上证50/中证1000 及多数有 ETF 跟踪的中证策略指数。
    weight 字段单位为百分比，统一 ÷100 转为 0-1 后返回，与 AkShare csindex
    快照口径一致；不支持的指数返回空列表由服务层降级到 AkShare/Baostock。
    """

    source_name = "tushare_index_member"

    def _ts_code_candidates(self, index_code: str) -> list[str]:
        """返回指数在 index_weight 中可能使用的 ts_code 候选。

        0 开头的代码既有上交所指数也有中证指数公司发布的指数（如 000852.SH
        与 000813.CSI 并存），依次尝试 .SH/.CSI；9/H 开头中证策略指数用 .CSI，
        其余按深市 .SZ 处理。

        Args:
            index_code: 指数代码，如 000300。

        Returns:
            按优先级排列的 ts_code 候选列表。
        """
        if index_code.startswith(("9", "H", "h")):
            return [f"{index_code}.CSI"]
        if index_code.startswith(("0",)):
            return [f"{index_code}.SH", f"{index_code}.CSI"]
        if index_code.startswith(("51", "56")):
            return [f"{index_code}.SH"]
        return [f"{index_code}.SZ"]

    def fetch_weight_snapshot(self, index_code: str, trade_date: date) -> list[dict[str, Any]]:
        """获取指数在指定交易日可用的最近一次月度成分/权重快照。

        Args:
            index_code: 指数代码，如 000300。
            trade_date: 目标交易日；返回不晚于该日的最近月度快照。

        Returns:
            [{stock_code, weight}]，weight 为 0-1；无可用快照时返回空列表。
        """
        if not self.is_configured():
            return []
        first_prev_month = trade_date.replace(day=1) - timedelta(days=1)
        first_prev_month = first_prev_month.replace(day=1)
        df = None
        for ts_code in self._ts_code_candidates(index_code):
            df = self._fetch(
                "index_weight",
                index_code=ts_code,
                start_date=first_prev_month.strftime("%Y%m%d"),
                end_date=trade_date.strftime("%Y%m%d"),
            )
            if df is not None and not df.empty:
                break
        if df is None or df.empty:
            return []
        df = df[df["trade_date"].astype(str) <= trade_date.strftime("%Y%m%d")]
        if df.empty:
            return []
        latest_date = str(df["trade_date"].max())
        df = df[df["trade_date"].astype(str) == latest_date]
        rows: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            con_code = str(row.get("con_code") or "")
            if not con_code or "." not in con_code:
                continue
            stock_code = con_code.split(".", 1)[0]
            weight = _to_float(row.get("weight"))
            rows.append(
                {
                    "stock_code": stock_code,
                    "weight": round(weight / 100.0, 6) if weight is not None else None,
                }
            )
        return rows


class TushareStockClient(_TushareBaseMixin, BaseDataClient):
    """Tushare Pro 个股数据客户端（stock_basic / daily / adj_factor / daily_basic / moneyflow）。

    覆盖沪深 A 股：daily 按交易日返回全市场行情（约 5500 行/日），也支持
    按 ts_code+日期区间拉单只股票历史。库存字段保持 Tushare 原生单位：
    daily.vol 为手、amount 为千元、ah_vol/ah_amount 为盘后成交；moneyflow
    量为手、金额为万元。前/后复权不在此客户端落库，由领域层使用复权因子计算。
    """

    source_name = "tushare_stock"

    @staticmethod
    def _split_ts_code(ts_code: Any) -> tuple[str, str] | None:
        """拆分 Tushare ts_code 为 6 位代码与交易所后缀。

        Args:
            ts_code: Tushare 代码，如 600000.SH。

        Returns:
            (stock_code, suffix)；格式非法时返回 None。
        """
        text = str(ts_code or "").strip().upper()
        if "." not in text:
            return None
        stock_code, suffix = text.split(".", 1)
        if not stock_code.isdigit() or suffix not in {"SH", "SZ", "BJ"}:
            return None
        return stock_code.zfill(6), suffix

    def _daily_rows(self, df: Any) -> list[dict[str, Any]]:
        """把 daily 返回 DataFrame 归一化为个股日线行。

        Args:
            df: Tushare daily 返回的 DataFrame。

        Returns:
            含 OHLC、量额、盘后成交与 source 的字典列表。
        """
        rows: list[dict[str, Any]] = []
        if df is None or df.empty:
            return rows
        for _, row in df.iterrows():
            split = self._split_ts_code(row.get("ts_code"))
            trade_date = _parse_yyyymmdd(row.get("trade_date"))
            if split is None or trade_date is None:
                continue
            stock_code, _suffix = split
            rows.append(
                {
                    "trade_date": trade_date,
                    "stock_code": stock_code,
                    "open": _to_float(row.get("open")),
                    "high": _to_float(row.get("high")),
                    "low": _to_float(row.get("low")),
                    "close": _to_float(row.get("close")),
                    "pre_close": _to_float(row.get("pre_close")),
                    "change": _to_float(row.get("change")),
                    "pct_chg": _to_float(row.get("pct_chg")),
                    "vol": _to_float(row.get("vol")),
                    "amount": _to_float(row.get("amount")),
                    "ah_vol": _to_float(row.get("ah_vol")),
                    "ah_amount": _to_float(row.get("ah_amount")),
                    "source": "tushare",
                }
            )
        rows.sort(key=lambda item: (item["trade_date"], item["stock_code"]))
        return rows

    def _daily_basic_rows(self, df: Any) -> list[dict[str, Any]]:
        """把 daily_basic 返回 DataFrame 归一化为每日指标行。

        Args:
            df: Tushare daily_basic 返回的 DataFrame。

        Returns:
            含全部每日指标字段的字典列表。
        """
        rows: list[dict[str, Any]] = []
        if df is None or df.empty:
            return rows
        for _, row in df.iterrows():
            split = self._split_ts_code(row.get("ts_code"))
            trade_date = _parse_yyyymmdd(row.get("trade_date"))
            if split is None or trade_date is None:
                continue
            stock_code, _suffix = split
            rows.append(
                {
                    "trade_date": trade_date,
                    "stock_code": stock_code,
                    "close": _to_float(row.get("close")),
                    "turnover_rate": _to_float(row.get("turnover_rate")),
                    "turnover_rate_f": _to_float(row.get("turnover_rate_f")),
                    "volume_ratio": _to_float(row.get("volume_ratio")),
                    "pe": _to_float(row.get("pe")),
                    "pe_ttm": _to_float(row.get("pe_ttm")),
                    "pb": _to_float(row.get("pb")),
                    "ps": _to_float(row.get("ps")),
                    "ps_ttm": _to_float(row.get("ps_ttm")),
                    "dv_ratio": _to_float(row.get("dv_ratio")),
                    "dv_ttm": _to_float(row.get("dv_ttm")),
                    "total_share": _to_float(row.get("total_share")),
                    "float_share": _to_float(row.get("float_share")),
                    "free_share": _to_float(row.get("free_share")),
                    "total_mv": _to_float(row.get("total_mv")),
                    "circ_mv": _to_float(row.get("circ_mv")),
                    "limit_status": _to_int(row.get("limit_status")),
                    "source": "tushare",
                }
            )
        rows.sort(key=lambda item: (item["trade_date"], item["stock_code"]))
        return rows

    def _moneyflow_rows(self, df: Any) -> list[dict[str, Any]]:
        """把 moneyflow 返回 DataFrame 归一化为资金流向行。

        Args:
            df: Tushare moneyflow 返回的 DataFrame。

        Returns:
            含全部资金流向字段的字典列表。
        """
        volume_fields = (
            "buy_sm_vol",
            "sell_sm_vol",
            "buy_md_vol",
            "sell_md_vol",
            "buy_lg_vol",
            "sell_lg_vol",
            "buy_elg_vol",
            "sell_elg_vol",
            "net_mf_vol",
        )
        amount_fields = (
            "buy_sm_amount",
            "sell_sm_amount",
            "buy_md_amount",
            "sell_md_amount",
            "buy_lg_amount",
            "sell_lg_amount",
            "buy_elg_amount",
            "sell_elg_amount",
            "net_mf_amount",
        )
        rows: list[dict[str, Any]] = []
        if df is None or df.empty:
            return rows
        for _, row in df.iterrows():
            split = self._split_ts_code(row.get("ts_code"))
            trade_date = _parse_yyyymmdd(row.get("trade_date"))
            if split is None or trade_date is None:
                continue
            stock_code, _suffix = split
            item: dict[str, Any] = {
                "trade_date": trade_date,
                "stock_code": stock_code,
                "source": "tushare",
            }
            item.update({field: _to_int(row.get(field)) for field in volume_fields})
            item.update({field: _to_float(row.get(field)) for field in amount_fields})
            rows.append(item)
        rows.sort(key=lambda item: (item["trade_date"], item["stock_code"]))
        return rows

    @staticmethod
    def _adj_factor_rows(df: Any) -> list[dict[str, Any]]:
        """把 adj_factor 返回 DataFrame 归一化为复权因子行。

        Args:
            df: Tushare adj_factor 返回的 DataFrame。

        Returns:
            [{trade_date, stock_code, adj_factor}]。
        """
        rows: list[dict[str, Any]] = []
        if df is None or df.empty:
            return rows
        for _, row in df.iterrows():
            split = TushareStockClient._split_ts_code(row.get("ts_code"))
            trade_date = _parse_yyyymmdd(row.get("trade_date"))
            factor = _to_float(row.get("adj_factor"))
            if split is None or trade_date is None:
                continue
            rows.append(
                {
                    "trade_date": trade_date,
                    "stock_code": split[0],
                    "adj_factor": factor,
                }
            )
        rows.sort(key=lambda item: (item["trade_date"], item["stock_code"]))
        return rows

    def fetch_daily_by_trade_date(self, trade_date: date) -> list[dict[str, Any]]:
        """拉取指定交易日全市场个股日线（含 OHLC、量额、盘后成交）。

        Args:
            trade_date: 交易日。

        Returns:
            Tushare daily 归一化行列表，source="tushare"。
        """
        df = self._fetch(
            "daily",
            trade_date=trade_date.strftime("%Y%m%d"),
            fields=_STOCK_DAILY_FIELDS,
        )
        return self._daily_rows(df)

    def fetch_close_by_trade_date(self, trade_date: date) -> list[dict[str, Any]]:
        """拉取指定交易日全市场收盘价（兼容旧调用，返回 close 轻量行）。

        Args:
            trade_date: 交易日。

        Returns:
            [{trade_date, stock_code, close, source}]，source="tushare"。
        """
        return [
            {
                "trade_date": row["trade_date"],
                "stock_code": row["stock_code"],
                "close": row["close"],
                "source": row["source"],
            }
            for row in self.fetch_daily_by_trade_date(trade_date)
            if row.get("close") is not None
        ]

    def fetch_history_daily(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """拉取单只股票历史日线（Tushare daily，ts_code 模式）。

        Args:
            stock_code: 6 位股票代码。
            start_date: 起始日 'YYYYMMDD'。
            end_date: 结束日 'YYYYMMDD'。

        Returns:
            按日期升序的日线行；未覆盖/无数据返回空列表。
        """
        suffix = _stock_ts_suffix(stock_code)
        if suffix is None or not self.is_configured():
            return []
        df = self._fetch(
            "daily",
            ts_code=f"{stock_code}.{suffix}",
            start_date=start_date,
            end_date=end_date,
            fields=_STOCK_DAILY_FIELDS,
        )
        return self._daily_rows(df)

    def fetch_history_close(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """拉取单只股票历史收盘价（兼容旧调用）。

        Args:
            stock_code: 6 位股票代码。
            start_date: 起始日 'YYYYMMDD'。
            end_date: 结束日 'YYYYMMDD'。

        Returns:
            [{trade_date, close}] 按日期升序；未覆盖/无数据返回空列表。
        """
        return [
            {"trade_date": row["trade_date"], "close": row["close"]}
            for row in self.fetch_history_daily(stock_code, start_date, end_date)
            if row.get("close") is not None
        ]

    def fetch_adj_factor_by_trade_date(self, trade_date: date) -> list[dict[str, Any]]:
        """拉取指定交易日全市场复权因子。

        Args:
            trade_date: 交易日。

        Returns:
            [{trade_date, stock_code, adj_factor}]。
        """
        df = self._fetch(
            "adj_factor",
            trade_date=trade_date.strftime("%Y%m%d"),
            fields=_STOCK_ADJ_FACTOR_FIELDS,
        )
        return self._adj_factor_rows(df)

    def fetch_history_adj_factor(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """拉取单只股票历史复权因子。

        Args:
            stock_code: 6 位股票代码。
            start_date: 起始日 'YYYYMMDD'。
            end_date: 结束日 'YYYYMMDD'。

        Returns:
            [{trade_date, stock_code, adj_factor}]。
        """
        suffix = _stock_ts_suffix(stock_code)
        if suffix is None or not self.is_configured():
            return []
        df = self._fetch(
            "adj_factor",
            ts_code=f"{stock_code}.{suffix}",
            start_date=start_date,
            end_date=end_date,
            fields=_STOCK_ADJ_FACTOR_FIELDS,
        )
        return self._adj_factor_rows(df)

    def fetch_daily_basic_by_trade_date(self, trade_date: date) -> list[dict[str, Any]]:
        """拉取指定交易日全市场每日指标。

        Args:
            trade_date: 交易日。

        Returns:
            Tushare daily_basic 归一化行列表。
        """
        df = self._fetch(
            "daily_basic",
            trade_date=trade_date.strftime("%Y%m%d"),
            fields=_STOCK_DAILY_BASIC_FIELDS,
        )
        return self._daily_basic_rows(df)

    def fetch_history_daily_basic(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """拉取单只股票历史每日指标。

        Args:
            stock_code: 6 位股票代码。
            start_date: 起始日 'YYYYMMDD'。
            end_date: 结束日 'YYYYMMDD'。

        Returns:
            按日期升序的每日指标行。
        """
        suffix = _stock_ts_suffix(stock_code)
        if suffix is None or not self.is_configured():
            return []
        df = self._fetch(
            "daily_basic",
            ts_code=f"{stock_code}.{suffix}",
            start_date=start_date,
            end_date=end_date,
            fields=_STOCK_DAILY_BASIC_FIELDS,
        )
        return self._daily_basic_rows(df)

    def fetch_moneyflow_by_trade_date(self, trade_date: date) -> list[dict[str, Any]]:
        """拉取指定交易日全市场个股资金流向。

        Args:
            trade_date: 交易日。

        Returns:
            Tushare moneyflow 归一化行列表。
        """
        df = self._fetch(
            "moneyflow",
            trade_date=trade_date.strftime("%Y%m%d"),
            fields=_STOCK_MONEYFLOW_FIELDS,
        )
        return self._moneyflow_rows(df)

    def fetch_history_moneyflow(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """拉取单只股票历史资金流向。

        Args:
            stock_code: 6 位股票代码。
            start_date: 起始日 'YYYYMMDD'。
            end_date: 结束日 'YYYYMMDD'。

        Returns:
            按日期升序的资金流向行。
        """
        suffix = _stock_ts_suffix(stock_code)
        if suffix is None or not self.is_configured():
            return []
        df = self._fetch(
            "moneyflow",
            ts_code=f"{stock_code}.{suffix}",
            start_date=start_date,
            end_date=end_date,
            fields=_STOCK_MONEYFLOW_FIELDS,
        )
        return self._moneyflow_rows(df)

    def fetch_stock_basics(self) -> list[dict[str, Any]]:
        """拉取沪深 A 股名单（上市 L + 退市 D）。

        只保留 `exchange` 为 SSE/SZSE 的标的，排除北交所与 CDR；退市名单
        保留 delist_date，供个股目录与质量快照使用。

        Returns:
            [{stock_code, ts_code, name_cn, market, exchange, ipo_date,
            delist_date, is_active, source}]。
        """
        results: list[dict[str, Any]] = []
        for status in ("L", "D"):
            df = self._fetch(
                "stock_basic",
                list_status=status,
                fields=_STOCK_BASIC_FIELDS,
            )
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                split = self._split_ts_code(row.get("ts_code"))
                if split is None:
                    continue
                stock_code, suffix = split
                exchange = str(row.get("exchange") or "").strip().upper()
                if exchange not in {"SSE", "SZSE"}:
                    continue
                market = str(row.get("market") or "").strip()
                if market == "CDR":
                    continue
                results.append(
                    {
                        "stock_code": stock_code,
                        "ts_code": f"{stock_code}.{suffix}",
                        "name_cn": str(row.get("name") or "").strip(),
                        "market": market or None,
                        "exchange": exchange,
                        "ipo_date": _parse_yyyymmdd(row.get("list_date")),
                        "delist_date": _parse_yyyymmdd(row.get("delist_date")),
                        "is_active": status == "L",
                        "source": "tushare",
                    }
                )
        return results


class TushareIndexBasicClient(_TushareBaseMixin, BaseDataClient):
    """Tushare Pro 指数基本信息客户端（index_basic，用于名称查询）。"""

    source_name = "tushare_index_basic"

    @staticmethod
    def _ts_code(index_code: str) -> str:
        """把指数代码转为 Tushare ts_code（沪/深/中证后缀规则）。"""
        if index_code.startswith(("9", "H", "h")):
            return f"{index_code}.CSI"
        if index_code.startswith(("0", "51", "56")):
            return f"{index_code}.SH"
        return f"{index_code}.SZ"

    def fetch_index_name(self, index_code: str) -> str | None:
        """查询指数中文名称。

        Args:
            index_code: 指数代码，如 000300。

        Returns:
            指数中文名称；接口无该指数或未配置 Token 时返回 None。
        """
        if not self.is_configured():
            return None
        df = self._fetch(
            "index_basic",
            ts_code=self._ts_code(index_code),
            fields="ts_code,name",
        )
        if df is None or df.empty:
            return None
        name = str(df.iloc[0].get("name") or "").strip()
        return name or None


def _to_float(value: Any) -> float | None:
    """把上游数值安全转为 float，NaN/None/空串返回 None。

    Args:
        value: 上游单元格值。

    Returns:
        有限数值；否则 None。
    """
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _to_int(value: Any) -> int | None:
    """把上游数值安全转为 int，NaN/None/空串返回 None。

    Args:
        value: 上游单元格值。

    Returns:
        整数值；无法转换时返回 None。
    """
    number = _to_float(value)
    return int(number) if number is not None else None


def today_cn() -> date:
    """返回北京时间当日日期（模块内轻量封装，避免重复导入链路）。"""
    from quant_etf_api.infra.time import today_cn as _today_cn

    return _today_cn()
