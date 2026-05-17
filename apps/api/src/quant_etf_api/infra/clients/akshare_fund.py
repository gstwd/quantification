from __future__ import annotations

import re
import time
from dataclasses import dataclass

import akshare as ak

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus


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
    """ETF 基金信息客户端（基于 AkShare SDK）。

    使用东方财富网页版接口（fund_overview_em）获取 ETF 基金档案信息，
    包括基金名称、类型、成立日期、基金公司、跟踪指数等。
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
            df = ak.fund_overview_em(symbol=code)
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

    def health_check(self) -> HealthStatus:
        """通过查询 510300 基金信息检测连通性。"""
        try:
            start = time.perf_counter()
            info = self.fetch_etf_info("510300")
            elapsed = (time.perf_counter() - start) * 1000
            ok = info is not None
            return HealthStatus(
                healthy=ok,
                message="AkShare 基金信息接口可达" if ok else "AkShare 基金信息接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
