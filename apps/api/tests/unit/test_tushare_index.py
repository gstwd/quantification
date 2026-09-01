"""测试 TushareIndexClient（Tushare Pro 指数日线）。"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pandas as pd

from quant_etf_api.infra.clients.index_daily_common import is_ohlc_complete
from quant_etf_api.infra.clients.tushare_index import TushareIndexClient


def _index_daily_df() -> pd.DataFrame:
    """构造 tushare pro.index_daily 返回（amount 单位千元）。"""
    return pd.DataFrame(
        {
            "ts_code": ["000300.SH"] * 3,
            "trade_date": ["20260105", "20260106", "20260107"],
            "close": [4717.75, 4790.69, 4776.67],
            "open": [4661.62, 4719.21, 4796.93],
            "high": [4721.64, 4790.69, 4802.60],
            "low": [4661.62, 4718.01, 4751.07],
            "pre_close": [4629.65, 4717.75, 4790.69],
            "change": [88.10, 72.94, -14.02],
            "pct_chg": [1.90, 1.55, -0.29],
            "vol": [234144839.0, 296970686.0, 259095564.0],  # 手
            "amount": [630577351.311, 725414566.873, 664897929.361],  # 千元
        }
    )


def _fake_tushare(df: pd.DataFrame):
    """构造 tushare 假模块（仅暴露 set_token / pro_api）。"""
    captured: dict = {}

    class _FakePro:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def index_daily(self, **kwargs) -> pd.DataFrame:
            """记录参数并返回预置数据。"""
            self.calls.append(kwargs)
            return df

    class _FakeTushare:
        def __init__(self) -> None:
            self.pro = _FakePro()

        def set_token(self, token: str) -> None:
            captured["token"] = token

        def pro_api(self):
            return self.pro

    return _FakeTushare(), captured


class TestTushareIndexClient:
    """Tushare Pro 指数日线客户端。"""

    def test_not_configured_returns_empty(self) -> None:
        """未配置 Token 时跳过该数据源（返回空列表）。"""
        client = TushareIndexClient(token=None)
        assert client.is_configured() is False
        assert client.fetch_index_daily("000300") == []

    def test_ts_code_mapping_and_amount_unit(self) -> None:
        """ts_code 后缀映射正确，成交额千元 ×1000 转元。"""
        fake, _ = _fake_tushare(_index_daily_df())
        with patch.dict(sys.modules, {"tushare": fake}):
            client = TushareIndexClient(token="test-token")
            bars = client.fetch_index_daily("000300", "20260101", "20260131")

        assert fake.pro.calls[0]["ts_code"] == "000300.SH"
        assert fake.pro.calls[0]["start_date"] == "20260101"
        assert fake.pro.calls[0]["end_date"] == "20260131"
        assert len(bars) == 3
        assert bars[0].volume == 234144839.0  # 手
        assert bars[0].turnover == 630577351311.0  # 千元 → 元
        assert is_ohlc_complete(bars) is True

    def test_sz_suffix(self) -> None:
        """深证指数使用 .SZ 后缀。"""
        fake, _ = _fake_tushare(_index_daily_df())
        with patch.dict(sys.modules, {"tushare": fake}):
            client = TushareIndexClient(token="test-token")
            client.fetch_index_daily("399001")
        assert fake.pro.calls[0]["ts_code"] == "399001.SZ"

    def test_csi_suffix_for_h_series(self) -> None:
        """H 开头中证策略指数使用 .CSI 后缀（如 H30269 红利低波）。"""
        fake, _ = _fake_tushare(_index_daily_df())
        with patch.dict(sys.modules, {"tushare": fake}):
            client = TushareIndexClient(token="test-token")
            client.fetch_index_daily("H30269")
        assert fake.pro.calls[0]["ts_code"] == "H30269.CSI"
