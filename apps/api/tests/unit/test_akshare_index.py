"""测试 AkShareIndexClient 指数日线与估值接口。

所有测试使用真实 AkShare 调用，不做 mock。
执行方式: pytest tests/unit/test_akshare_index.py -v
"""

import time
from datetime import date

import pandas as pd
import pytest

from quant_etf_api.infra.clients.akshare_index import (
    AkShareIndexClient,
    IndexDailyBar,
    IndexValuation,
    _build_index_bars,
    _calc_percentile,
    _incremental_start_date,
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


class TestCalcPercentile:
    """统一百分位算法（B8）纯逻辑测试。"""

    @staticmethod
    def _series(values: list[float]) -> list[tuple]:
        """构造 (date, value) 序列，日期从 2025-01-01 起递增。"""
        from datetime import date, timedelta

        start = date(2025, 1, 1)
        return [(start + timedelta(days=i), v) for i, v in enumerate(values)]

    def test_first_day_neutral(self) -> None:
        """首日无历史可比，返回中性占位 50.0。"""
        data = self._series([3.5])
        assert _calc_percentile(data) == {data[0][0]: 50.0}

    def test_max_reaches_100(self) -> None:
        """单调上升时最高值百分位可达 100。"""
        data = self._series([1.0, 2.0, 3.0, 4.0])
        result = _calc_percentile(data)
        assert result[data[-1][0]] == 100.0

    def test_min_is_0(self) -> None:
        """单调下降时最低值百分位为 0。"""
        data = self._series([4.0, 3.0, 2.0, 1.0])
        result = _calc_percentile(data)
        assert result[data[-1][0]] == 0.0

    def test_ties_max_still_reaches_100(self) -> None:
        """并列最高值也计入自身，最高值百分位仍可达 100。"""
        data = self._series([1.0, 2.0, 2.0])
        result = _calc_percentile(data)
        assert result[data[-1][0]] == 100.0

    def test_expanding_window_no_lookahead(self) -> None:
        """百分位只使用截至当日的数据，不使用未来数据。"""
        data = self._series([1.0, 3.0, 2.0])
        result = _calc_percentile(data)
        # 第 3 日值 2.0：样本为 [1,3,2]，rank=1 → 1/2*100 = 50
        assert result[data[2][0]] == 50.0

    def test_fractional_percentile(self) -> None:
        """非极值返回保留两位小数的百分位。"""
        data = self._series([2.0, 4.0, 1.0, 3.0])
        result = _calc_percentile(data)
        # 值 3.0（第 4 日）：样本 [2,4,1,3]，rank=2 → 2/3*100 = 66.67
        assert result[data[3][0]] == 66.67


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

    def test_fetch_index_daily_since(self, client: AkShareIndexClient) -> None:
        """增量拉取带缓冲窗口，since 之后的 bar 涨跌幅可算。"""
        since = date(2026, 1, 5)
        bars = _retry_fetch(lambda: client.fetch_index_daily_since("000300", since))
        assert len(bars) > 0
        assert bars[0].trade_date >= _incremental_start_date(since)
        assert bars[-1].trade_date >= since
        # 缓冲窗口保证 since 之后每根 bar 都有前收盘
        for b in bars:
            if b.trade_date > since:
                assert b.prev_close_price is not None

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

    def test_sina_covers_shenzhen(self, client: AkShareIndexClient) -> None:
        """新浪源兜底覆盖深证指数（中证官网不支持 399xxx）。"""
        bars = _retry_fetch(
            lambda: client._fetch_index_daily_sina("399001", "20260101", "20260331")
        )
        assert len(bars) > 0
        assert bars[0].trade_date >= date(2026, 1, 1)
        assert bars[-1].trade_date <= date(2026, 3, 31)

    def test_zh_a_hist_general(self, client: AkShareIndexClient) -> None:
        """东方财富通用接口接受纯指数代码。"""
        try:
            bars = _retry_fetch(
                lambda: client._fetch_index_daily_zh_a_hist("000016", "20260101", "20260331")
            )
        except Exception:
            pytest.skip("东方财富接口在当前网络环境不可用")
        assert len(bars) > 0
        assert bars[0].trade_date >= date(2026, 1, 1)


class TestBuildIndexBars:
    """共享 DataFrame→Bar 转换助手（纯逻辑，无需网络）。"""

    def test_mapping_and_pct(self) -> None:
        """列名映射、日期解析与涨跌幅计算正确。"""
        df = pd.DataFrame(
            {
                "date": ["2026-01-05", "2026-01-06", "2026-01-07"],
                "open": [100.0, 101.0, 102.0],
                "close": [101.0, 102.0, 101.5],
                "high": [102.0, 103.0, 103.0],
                "low": [99.0, 100.0, 100.5],
                "volume": [1000, 1100, 1200],
            }
        )
        bars = _build_index_bars(df, "date", "open", "close", "high", "low", volume_col="volume")
        assert len(bars) == 3
        assert bars[0].trade_date == date(2026, 1, 5)
        assert bars[0].prev_close_price is None
        assert bars[0].change_pct is None
        assert bars[1].prev_close_price == 101.0
        assert bars[1].change_pct == round((102.0 - 101.0) / 101.0 * 100, 4)
        # 无成交额列 → 补 0；成交量按列名映射
        assert bars[0].turnover == 0.0
        assert bars[0].volume == 1000.0

    def test_local_date_filter(self) -> None:
        """本地日期过滤（新浪等无服务端过滤的源使用）。"""
        df = pd.DataFrame(
            {
                "date": ["2026-01-05", "2026-01-06", "2026-01-07"],
                "open": [1.0, 2.0, 3.0],
                "close": [1.0, 2.0, 3.0],
                "high": [1.0, 2.0, 3.0],
                "low": [1.0, 2.0, 3.0],
            }
        )
        bars = _build_index_bars(
            df,
            "date",
            "open",
            "close",
            "high",
            "low",
            start_date="20260106",
            end_date="20260106",
        )
        assert len(bars) == 1
        assert bars[0].trade_date == date(2026, 1, 6)

    def test_empty_df(self) -> None:
        """空 DataFrame 返回空列表。"""
        bars = _build_index_bars(pd.DataFrame(), "date", "open", "close", "high", "low")
        assert bars == []


class TestClientOptimizations:
    """第一轮评审优化点的纯逻辑测试。"""

    def test_incremental_start_date(self) -> None:
        """增量缓冲窗口回退 10 个自然日。"""
        assert _incremental_start_date(date(2026, 1, 15)) == date(2026, 1, 5)

    def test_call_with_timeout_returns_fast(self, client: AkShareIndexClient) -> None:
        """超时调用在限定时间内返回，不阻塞等待后台线程。"""
        t0 = time.perf_counter()
        with pytest.raises(TimeoutError):
            client._call_with_timeout(lambda: time.sleep(2), timeout=0.3)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0


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
