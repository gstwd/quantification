"""测试 EfinanceIndexClient（东方财富指数日线）。"""

from __future__ import annotations

import sys
from datetime import date
from unittest.mock import patch

import pandas as pd

from quant_etf_api.infra.clients.efinance_index import EfinanceIndexClient
from quant_etf_api.infra.clients.index_daily_common import is_ohlc_complete


def _fake_efinance(df: pd.DataFrame):
    """构造 efinance 假模块（仅暴露指数日线所需的 get_quote_history）。"""

    class _FakeStock:
        def __init__(self, result: pd.DataFrame) -> None:
            self._result = result

        def get_quote_history(self, *args, **kwargs) -> pd.DataFrame:
            """返回预先构造的日线 DataFrame。"""
            return self._result

    class _FakeEfinance:
        def __init__(self, result: pd.DataFrame) -> None:
            self.stock = _FakeStock(result)

    return _FakeEfinance(df)


def _index_df() -> pd.DataFrame:
    """构造 efinance 指数日线返回（东财列名）。"""
    return pd.DataFrame(
        {
            "股票代码": ["000300"] * 3,
            "股票名称": ["沪深300"] * 3,
            "日期": ["2026-01-05", "2026-01-06", "2026-01-07"],
            "开盘": [4661.62, 4719.21, 4796.93],
            "收盘": [4717.75, 4790.69, 4776.67],
            "最高": [4721.64, 4790.69, 4802.60],
            "最低": [4661.62, 4718.01, 4751.07],
            "成交量": [234144839.0, 296970686.0, 259095564.0],
            "成交额": [6.30577351311e11, 7.25414566873e11, 6.64897929361e11],
            "振幅": [1.28, 1.54, 1.08],
            "涨跌幅": [1.90, 1.55, -0.29],
            "涨跌额": [88.10, 72.94, -14.02],
            "换手率": [0.0, 0.0, 0.0],
        }
    )


class TestEfinanceIndexClient:
    """东方财富指数日线客户端。"""

    def test_secid_mapping(self) -> None:
        """沪市指数 secid=1，深市指数 secid=0。"""
        assert EfinanceIndexClient._to_secid("000300") == "1.000300"
        assert EfinanceIndexClient._to_secid("399001") == "0.399001"
        assert EfinanceIndexClient._to_secid("931743") == "1.931743"

    def test_fetch_index_daily_uses_secid_and_units(self) -> None:
        """调用 efinance 时直传 secid，成交量/成交额保持手/元口径。"""
        captured: dict = {}

        class _FakeStock:
            def get_quote_history(self, *args, **kwargs) -> pd.DataFrame:
                captured["args"] = args
                captured["kwargs"] = kwargs
                return _index_df()

        class _FakeEfinance:
            stock = _FakeStock()

        with patch.dict(sys.modules, {"efinance": _FakeEfinance()}):
            client = EfinanceIndexClient()
            bars = client.fetch_index_daily("000300", "20260101", "20260131")

        assert captured["args"][0] == "1.000300"
        assert captured["kwargs"]["beg"] == "2026-01-01"
        assert captured["kwargs"]["end"] == "2026-01-31"
        assert captured["kwargs"]["quote_id_mode"] is True
        assert len(bars) == 3
        assert bars[0].trade_date == date(2026, 1, 5)
        assert bars[0].volume == 234144839.0  # 手
        assert bars[0].turnover == 6.30577351311e11  # 元
        assert is_ohlc_complete(bars) is True

    def test_empty_df_returns_empty(self) -> None:
        """上游返回空 DataFrame 时返回空列表。"""
        with patch.dict(sys.modules, {"efinance": _fake_efinance(pd.DataFrame())}):
            client = EfinanceIndexClient()
            assert client.fetch_index_daily("000300") == []

    def test_fetch_index_daily_since_buffer(self) -> None:
        """增量拉取从缓冲窗口起点开始（起点回退 10 个自然日）。"""
        captured: dict = {}

        class _FakeStock:
            def get_quote_history(self, *args, **kwargs) -> pd.DataFrame:
                captured["kwargs"] = kwargs
                return _index_df()

        class _FakeEfinance:
            stock = _FakeStock()

        with patch.dict(sys.modules, {"efinance": _FakeEfinance()}):
            client = EfinanceIndexClient()
            client.fetch_index_daily_since("000300", date(2026, 1, 20))
        assert captured["kwargs"]["beg"] == "2026-01-10"  # 01-20 回退 10 天
