from __future__ import annotations

import re
import time
from dataclasses import dataclass

import akshare as ak

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus


@dataclass
class MacroIndicator:
    """宏观指标数据结构。"""

    indicator_code: str
    indicator_name: str
    period: str
    value: float
    unit: str | None
    period_date: str | None = None  # 标准化日期：CPI/PMI 取当月首日，LPR 取报价日 ("YYYY-MM-DD")


class AkShareMacroClient(BaseDataClient):
    """宏观指标客户端（基于 AkShare SDK）。

    封装 akshare 的 CPI（月度）、PMI（月度）、LPR 接口，
    统一返回 MacroIndicator 列表。
    """

    source_name = "akshare_macro"

    # ------------------------------------------------------------------
    # CPI 月度
    # ------------------------------------------------------------------

    def fetch_cpi_monthly(self) -> list[MacroIndicator]:
        """拉取中国月度 CPI 同比数据。

        Returns:
            MacroIndicator 列表，period 格式为 'YYYY-MM'
        """
        endpoint = "macro_china_cpi_monthly"
        self._log_request(endpoint)
        start = time.perf_counter()
        try:
            df = ak.macro_china_cpi_monthly()
        except Exception as e:
            self._log_error(endpoint, e, (time.perf_counter() - start) * 1000)
            raise

        results: list[MacroIndicator] = []
        for _, row in df.iterrows():
            d = row["日期"]
            val = row["今值"]
            if val is None or (isinstance(val, float) and val != val):
                continue
            period = d if isinstance(d, str) else d.strftime("%Y-%m")
            period_date = f"{period}-01"  # 统一为当月首日
            results.append(
                MacroIndicator(
                    indicator_code="cpi",
                    indicator_name="居民消费价格指数(CPI)同比",
                    period=period,
                    value=float(val),
                    unit="%",
                    period_date=period_date,
                )
            )
        elapsed = (time.perf_counter() - start) * 1000
        self._log_response(endpoint, len(results), elapsed)
        return results

    # ------------------------------------------------------------------
    # PMI 月度
    # ------------------------------------------------------------------

    def fetch_pmi(self) -> list[MacroIndicator]:
        """拉取中国制造业 PMI 月度数据。

        Returns:
            MacroIndicator 列表，period 格式为 'YYYY-MM'
        """
        endpoint = "macro_china_pmi"
        self._log_request(endpoint)
        start = time.perf_counter()
        try:
            df = ak.macro_china_pmi()
        except Exception as e:
            self._log_error(endpoint, e, (time.perf_counter() - start) * 1000)
            raise

        results: list[MacroIndicator] = []
        for _, row in df.iterrows():
            raw_period = str(row["月份"])
            # "2008年03月份" → "2008-03"
            m = re.match(r"(\d{4})年(\d{2})月份", raw_period)
            if not m:
                continue
            period = f"{m.group(1)}-{m.group(2)}"
            period_date = f"{period}-01"  # 统一为当月首日
            val = row["制造业-指数"]
            if val is None or (isinstance(val, float) and val != val):
                continue
            results.append(
                MacroIndicator(
                    indicator_code="pmi",
                    indicator_name="制造业采购经理指数(PMI)",
                    period=period,
                    value=float(val),
                    unit="%",
                    period_date=period_date,
                )
            )
        elapsed = (time.perf_counter() - start) * 1000
        self._log_response(endpoint, len(results), elapsed)
        return results

    # ------------------------------------------------------------------
    # LPR
    # ------------------------------------------------------------------

    def fetch_lpr(self) -> list[MacroIndicator]:
        """拉取中国 LPR（贷款市场报价利率）数据。

        Returns:
            MacroIndicator 列表，period 格式为 'YYYY-MM-DD'，
            每条包含 1 年期和 5 年期两个指标
        """
        endpoint = "macro_china_lpr"
        self._log_request(endpoint)
        start = time.perf_counter()
        try:
            df = ak.macro_china_lpr()
        except Exception as e:
            self._log_error(endpoint, e, (time.perf_counter() - start) * 1000)
            raise

        results: list[MacroIndicator] = []
        for _, row in df.iterrows():
            trade_date = str(row["TRADE_DATE"])
            lpr1y = row["LPR1Y"]
            lpr5y = row["LPR5Y"]
            if lpr1y is not None and not (isinstance(lpr1y, float) and lpr1y != lpr1y):
                results.append(
                    MacroIndicator(
                        indicator_code="lpr1y",
                        indicator_name="贷款市场报价利率(LPR) 1年期",
                        period=trade_date,
                        value=float(lpr1y),
                        unit="%",
                        period_date=trade_date,  # LPR 报价日即为 period_date
                    )
                )
            if lpr5y is not None and not (isinstance(lpr5y, float) and lpr5y != lpr5y):
                results.append(
                    MacroIndicator(
                        indicator_code="lpr5y",
                        indicator_name="贷款市场报价利率(LPR) 5年期",
                        period=trade_date,
                        value=float(lpr5y),
                        unit="%",
                        period_date=trade_date,
                    )
                )
        elapsed = (time.perf_counter() - start) * 1000
        self._log_response(endpoint, len(results), elapsed)
        return results

    # ------------------------------------------------------------------
    # 便捷方法：拉取所有宏观指标
    # ------------------------------------------------------------------

    def fetch_all(self) -> list[MacroIndicator]:
        """拉取所有宏观指标（CPI + PMI + LPR）。

        Returns:
            合并后的 MacroIndicator 列表
        """
        all_indicators: list[MacroIndicator] = []
        for name, fetcher in [
            ("CPI", self.fetch_cpi_monthly),
            ("PMI", self.fetch_pmi),
            ("LPR", self.fetch_lpr),
        ]:
            try:
                all_indicators.extend(fetcher())
            except Exception as e:
                self._logger.warning("拉取 %s 失败: %s", name, e)
        return all_indicators

    # ------------------------------------------------------------------
    # 健康检测
    # ------------------------------------------------------------------

    def health_check(self) -> HealthStatus:
        """通过拉取 PMI 数据检测连通性。"""
        try:
            start = time.perf_counter()
            indicators = self.fetch_pmi()
            elapsed = (time.perf_counter() - start) * 1000
            ok = len(indicators) > 0
            return HealthStatus(
                healthy=ok,
                message="AkShare 宏观接口可达" if ok else "AkShare 宏观接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
