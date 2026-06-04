"""测试 AkShareIndexClient 指数日线与估值接口。

所有测试使用真实 AkShare 调用，不做 mock。
执行方式: pytest tests/unit/test_akshare_index.py -v
"""

import time

import pytest

from quant_etf_api.infra.clients.akshare_index import (
    AkShareIndexClient,
    IndexDailyBar,
    IndexValuation,
)


def _retry_fetch(fn, max_retries: int = 2):
    """上游网络不稳定时的简单重试机制。"""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception:
            if attempt == max_retries:
                raise
            time.sleep(3)


@pytest.fixture(scope="module")
def client() -> AkShareIndexClient:
    return AkShareIndexClient()


class TestSourceName:
    """source_name 属性。"""

    def test_source_name(self, client: AkShareIndexClient) -> None:
        assert client.source_name == "akshare_index"


class TestIndexDaily:
    """指数日线数据拉取。"""

    @pytest.mark.parametrize("index_code", ["000300", "000016", "399001"])
    def test_fetch_returns_data(self, client: AkShareIndexClient, index_code: str) -> None:
        """验证主流指数能正常拉取日线数据。"""
        bars = _retry_fetch(lambda: client.fetch_index_daily(index_code))
        assert len(bars) > 0, f"指数 {index_code} 应返回日线数据"
        latest = bars[-1]
        assert isinstance(latest, IndexDailyBar)
        assert latest.close_price > 0
        assert latest.trade_date is not None

    def test_fetch_with_date_range(self, client: AkShareIndexClient) -> None:
        """验证日期过滤功能。"""
        bars = _retry_fetch(
            lambda: client.fetch_index_daily("000300", start_date="20260101", end_date="20260331")
        )
        if len(bars) > 0:
            assert str(bars[0].trade_date) >= "2026-01-01"
            assert str(bars[-1].trade_date) <= "2026-03-31"

    def test_prev_close_and_change_pct_populated(self, client: AkShareIndexClient) -> None:
        """验证 prev_close_price 和 change_pct 字段被正确填充。"""
        bars = _retry_fetch(lambda: client.fetch_index_daily("000300"))
        assert len(bars) > 1, "应返回多条日线数据"
        # 第一根 bar 无前收盘，应为 None
        assert bars[0].prev_close_price is None
        assert bars[0].change_pct is None
        # 第二根 bar 应有前收盘和涨跌幅
        assert bars[1].prev_close_price is not None
        assert bars[1].prev_close_price > 0
        assert bars[1].change_pct is not None


class TestIndexValuation:
    """指数估值数据拉取。

    估值数据来源于 legulegu.com，仅 沪深300/上证50/中证500 有稳定数据。
    中证500 在上游偶有返回异常，单独处理。
    """

    @pytest.mark.parametrize("index_code", ["000300", "000016"])
    def test_fetch_valuation_stable(self, client: AkShareIndexClient, index_code: str) -> None:
        """验证沪深300/上证50能稳定拉取 PE/PB 估值。"""
        valuations = _retry_fetch(lambda: client.fetch_index_valuation(index_code))
        assert len(valuations) > 0, f"指数 {index_code} 应返回估值数据"
        latest = valuations[-1]
        assert isinstance(latest, IndexValuation)
        assert latest.trade_date is not None

    def test_fetch_valuation_000905(self, client: AkShareIndexClient) -> None:
        """中证500 估值（上游偶有异常，容错处理）。"""
        try:
            valuations = client.fetch_index_valuation("000905")
        except Exception:
            pytest.skip("上游 legulegu.com 中证500 估值暂时不可用")
        if len(valuations) == 0:
            pytest.skip("上游 legulegu.com 中证500 估值返回空")
        assert valuations[-1].trade_date is not None

    def test_unsupported_index_returns_empty(self, client: AkShareIndexClient) -> None:
        """不支持的指数应返回空列表。"""
        valuations = client.fetch_index_valuation("000688")
        assert valuations == []

    def test_percentile_calculated(self, client: AkShareIndexClient) -> None:
        """验证 PE/PB 百分位被正确计算。"""
        try:
            valuations = _retry_fetch(lambda: client.fetch_index_valuation("000300"))
        except Exception:
            pytest.skip("上游 legulegu.com 暂时不可用")
        if len(valuations) < 10:
            pytest.skip("上游返回数据不足")
        # 检查中间某条数据的百分位是否被计算
        mid = valuations[len(valuations) // 2]
        assert mid.pe_percentile is not None, "PE 百分位应被计算"
        assert 0 <= mid.pe_percentile <= 100, "PE 百分位应在 0-100 之间"
        assert mid.pb_percentile is not None, "PB 百分位应被计算"
        assert 0 <= mid.pb_percentile <= 100, "PB 百分位应在 0-100 之间"


class TestHealthCheck:
    """连通性检测。"""

    def test_health_check_healthy(self, client: AkShareIndexClient) -> None:
        status = client.health_check()
        assert status.healthy is True
        assert status.latency_ms is not None and status.latency_ms > 0
