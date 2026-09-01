"""测试 BaostockIndexClient（Baostock 指数日线）。"""

from __future__ import annotations

import sys
from datetime import date
from unittest.mock import patch

from quant_etf_api.infra.clients.baostock_index import BaostockIndexClient
from quant_etf_api.infra.clients.index_daily_common import is_ohlc_complete


class _FakeBaostock:
    """模拟 baostock 模块：记录查询参数并按需返回行数据。"""

    def __init__(self, rows: list[list[str]], fields: list[str]) -> None:
        self.rows = rows
        self.fields = fields
        self.query_params: dict = {}
        self.login_called = 0
        self.logout_called = 0
        self._idx = 0

    def login(self):
        """模拟登录成功。"""
        self.login_called += 1
        return type("_Rs", (), {"error_code": "0", "error_msg": ""})()

    def logout(self) -> None:
        """模拟登出。"""
        self.logout_called += 1

    def query_history_k_data_plus(self, **kwargs):
        """记录参数并返回结果集对象。"""
        self.query_params = kwargs
        return self

    def next(self) -> bool:
        """逐行迭代。"""
        if self._idx < len(self.rows):
            self._idx += 1
            return True
        return False

    def get_row_data(self) -> list[str]:
        """返回当前行。"""
        return self.rows[self._idx - 1]

    @property
    def error_code(self) -> str:
        """查询成功码。"""
        return "0"

    @property
    def error_msg(self) -> str:
        """错误信息。"""
        return ""


_FIELDS = ["date", "open", "high", "low", "close", "volume", "amount", "pctChg"]


def _rows() -> list[list[str]]:
    """构造 baostock 指数日线行（volume 单位股、amount 单位元）。"""
    return [
        [
            "2026-01-05",
            "4661.6160",
            "4721.6378",
            "4661.6160",
            "4717.7457",
            "23414483900",
            "630577351311.4",
            "1.896487",
        ],
        [
            "2026-01-06",
            "4719.2088",
            "4790.6938",
            "4718.0099",
            "4790.6938",
            "29697068600",
            "725414566872.9",
            "1.546249",
        ],
    ]


class TestBaostockIndexClient:
    """Baostock 指数日线客户端。"""

    def test_code_mapping_and_unit_normalization(self) -> None:
        """代码映射、股→手换算、字符串数值化正确。"""
        fake = _FakeBaostock(_rows(), _FIELDS)
        with patch.dict(sys.modules, {"baostock": fake}):
            client = BaostockIndexClient()
            bars = client.fetch_index_daily("000300", "20260101", "20260131")

        assert fake.query_params["code"] == "sh.000300"
        assert fake.query_params["start_date"] == "2026-01-01"
        assert fake.query_params["end_date"] == "2026-01-31"
        assert fake.query_params["frequency"] == "d"
        assert fake.query_params["adjustflag"] == "3"
        assert fake.login_called == 1
        assert fake.logout_called == 1
        assert len(bars) == 2
        assert bars[0].trade_date == date(2026, 1, 5)
        assert bars[0].volume == 234144839.0  # 股 ÷ 100 → 手
        assert bars[0].turnover == 630577351311.4  # 元
        assert is_ohlc_complete(bars) is True

    def test_sz_prefix(self) -> None:
        """深证指数使用 sz. 前缀。"""
        fake = _FakeBaostock(_rows(), _FIELDS)
        with patch.dict(sys.modules, {"baostock": fake}):
            client = BaostockIndexClient()
            client.fetch_index_daily("399006")
        assert fake.query_params["code"] == "sz.399006"

    def test_empty_result_returns_empty(self) -> None:
        """查询无数据时返回空列表。"""
        fake = _FakeBaostock([], _FIELDS)
        with patch.dict(sys.modules, {"baostock": fake}):
            client = BaostockIndexClient()
            assert client.fetch_index_daily("000300") == []
