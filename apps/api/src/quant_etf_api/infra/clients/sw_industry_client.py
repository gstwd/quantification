"""申万行业数据客户端（行业指数日线 + 行业分类历史）。

申万官网部分接口存在 SSL 证书校验问题，分类文件请求显式关闭校验
（与复现工程相同）；指数日线走 akshare index_hist_sw（其内部已 verify=False）。
"""

from __future__ import annotations

import io
import time
from datetime import date
from typing import Any

import akshare as ak
import pandas as pd
import requests
import urllib3

from quant_etf_api.domain.industry.constants import normalize_sw_code
from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus

urllib3.disable_warnings()

_CLASSIFICATION_URL = (
    "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
)
# 申万官网风控要求浏览器风格请求头；缺失时可能返回 508/502
_CLASSIFICATION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": ("https://www.swsresearch.com/institute_sw/allIndex/downloadCenter/industryType"),
    "Accept": "*/*",
}


class SwIndustryClient(BaseDataClient):
    """申万行业指数与分类历史客户端。"""

    source_name = "akshare_sw_industry"

    def fetch_daily(
        self,
        industry_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """拉取单个申万一级行业指数日线。

        Args:
            industry_code: 申万一级行业指数代码，如 801010（可带 .SI 后缀）。
            start_date: 起始日 'YYYYMMDD'，None 表示全部历史。
            end_date: 结束日 'YYYYMMDD'，None 表示最新。

        Returns:
            按日期升序的日线字典列表（open/high/low/close/volume/turnover）。
        """
        symbol = normalize_sw_code(industry_code)
        endpoint = "index_hist_sw"
        self._log_request(endpoint, {"symbol": symbol})
        start = time.perf_counter()
        try:
            df = ak.index_hist_sw(symbol=symbol, period="day")
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.date
            if start_date:
                df = df[df["日期"] >= date.fromisoformat(start_date)]
            if end_date:
                df = df[df["日期"] <= date.fromisoformat(end_date)]
            rows: list[dict[str, Any]] = []
            for _, row in df.iterrows():
                trade_date = row["日期"]
                if not isinstance(trade_date, date):
                    continue
                rows.append(
                    {
                        "trade_date": trade_date,
                        "industry_code": symbol,
                        "open_price": _to_float(row.get("开盘")),
                        "high_price": _to_float(row.get("最高")),
                        "low_price": _to_float(row.get("最低")),
                        "close_price": _to_float(row.get("收盘")),
                        "volume": _to_float(row.get("成交量")),
                        "turnover": _to_float(row.get("成交额")),
                        "source": self.source_name,
                    }
                )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(rows), elapsed)
            return rows
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def fetch_first_info(self) -> pd.DataFrame:
        """拉取申万一级行业目录（代码/名称），失败时返回空表。"""
        try:
            df = ak.sw_index_first_info()
            df = df.rename(columns={df.columns[0]: "code", df.columns[1]: "name"})
            df["code"] = df["code"].astype(str).map(normalize_sw_code)
            return df[["code", "name"]]
        except Exception as e:
            self._log_error("sw_index_first_info", e, 0.0)
            return pd.DataFrame(columns=["code", "name"])

    def fetch_classification_events(self) -> pd.DataFrame:
        """拉取申万全部行业分类变动历史并归一化字段。

        Returns:
            DataFrame，列为 symbol/start_date/industry_code/update_time；
            start_date 与 update_time 为 date 对象。
        """
        endpoint = "stock_industry_clf_hist_sw"
        self._log_request(endpoint, {})
        start = time.perf_counter()
        try:
            # 申万官网偶发 508/502 风控响应，逐个候选 URL 重试最多 6 次
            candidates = [_CLASSIFICATION_URL, f"{_CLASSIFICATION_URL}?t=1"]
            content: bytes | None = None
            last_error: Exception | None = None
            for _ in range(3):
                for url in candidates:
                    try:
                        resp = requests.get(
                            url,
                            headers=_CLASSIFICATION_HEADERS,
                            timeout=30,
                            verify=False,
                        )
                        if resp.status_code == 200:
                            content = resp.content
                            break
                        last_error = requests.HTTPError(
                            f"{resp.status_code} Server Error for url: {url}"
                        )
                    except requests.RequestException as exc:
                        last_error = exc
                if content is not None:
                    break
                time.sleep(2.0)
            if content is None:
                raise last_error or RuntimeError("申万分类文件下载失败")
            raw = pd.read_excel(
                io.BytesIO(content),
                dtype={"股票代码": "str", "行业代码": "str"},
            )
            raw = raw.rename(
                columns={
                    "股票代码": "symbol",
                    "计入日期": "start_date",
                    "行业代码": "industry_code",
                    "更新日期": "update_time",
                }
            )
            raw["symbol"] = raw["symbol"].astype(str).str.zfill(6)
            raw["industry_code"] = raw["industry_code"].astype(str)
            raw["start_date"] = pd.to_datetime(raw["start_date"], errors="coerce").dt.date
            raw["update_time"] = pd.to_datetime(raw["update_time"], errors="coerce").dt.date
            raw = raw.dropna(subset=["symbol", "industry_code", "start_date"])
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(raw), elapsed)
            return raw
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    def health_check(self) -> HealthStatus:
        """通过拉取农林牧渔最近行情检测连通性。"""
        try:
            rows = self.fetch_daily("801010")
            return HealthStatus(
                healthy=len(rows) > 0,
                message="申万行业接口可达" if rows else "申万行业接口返回空数据",
                latency_ms=0.0,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))


def _to_float(value: Any) -> float | None:
    """将可空值转为 float，NaN/None 返回 None。"""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:  # NaN 检查
        return None
    return num
