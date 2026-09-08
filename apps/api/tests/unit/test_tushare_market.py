"""测试 Tushare 市场数据客户端（估值 / 宏观 / 成分 / 个股）。"""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd
import pytest

import quant_etf_api.infra.clients.tushare_market as market
from quant_etf_api.infra.clients.tushare_market import (
    TushareIndexMemberClient,
    TushareIndexValuationClient,
    TushareMacroClient,
    TushareStockClient,
)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch) -> None:
    """关闭实例级节流，避免单测因等待过慢。"""
    monkeypatch.setattr(market, "_TUSHARE_MIN_INTERVAL", 0.0)
    TushareIndexValuationClient.clear_cache()


def _make_fake_tushare(handlers: dict) -> tuple[object, object]:
    """构造可替换 sys.modules['tushare'] 的假模块。

    Args:
        handlers: endpoint 名 → 可调用对象（接收 kwargs 返回 DataFrame）。

    Returns:
        (假 tushare 模块, 假 pro 实例)。
    """

    class _FakePro:
        def __init__(self) -> None:
            self.calls: dict[str, list[dict]] = {}

        def __getattr__(self, endpoint: str):
            def caller(**kwargs):
                self.calls.setdefault(endpoint, []).append(kwargs)
                handler = handlers.get(endpoint)
                return handler(**kwargs) if handler else pd.DataFrame()

            return caller

    class _FakeTushare:
        def __init__(self) -> None:
            self.pro = _FakePro()
            self.token: str | None = None

        def set_token(self, token: str) -> None:
            self.token = token

        def pro_api(self):
            return self.pro

    module = _FakeTushare()
    return module, module.pro


def _dailybasic_df(days: int = 3, base_pe: float = 12.0) -> pd.DataFrame:
    """构造 index_dailybasic 返回（含 pe_ttm/pb）。"""
    return pd.DataFrame(
        {
            "ts_code": ["000300.SH"] * days,
            "trade_date": ["2026090%d" % (2 + i) for i in range(days)],
            "pe": [base_pe + 2 + i for i in range(days)],
            "pe_ttm": [base_pe + i for i in range(days)],
            "pb": [1.2 + i * 0.1 for i in range(days)],
        }
    )


class TestTushareIndexValuationClient:
    """Tushare 指数估值客户端。"""

    def test_supports_known_codes(self) -> None:
        """仅声明 000300/000016/000905 三个基准指数支持 Tushare 估值。"""
        client = TushareIndexValuationClient(token="test")
        assert client.supports("000300") is True
        assert client.supports("000016") is True
        assert client.supports("000905") is True
        assert client.supports("000852") is False

    def test_fetch_uses_pe_ttm_and_source(self, monkeypatch) -> None:
        """pe 取 pe_ttm、pb 直取，source=tushare，百分位可用。"""
        def handler(**kwargs):
            # 只在 2026 年窗口返回数据，其余年份为空，模拟全历史分页
            if str(kwargs.get("start_date", "")).startswith("2026"):
                return _dailybasic_df()
            return pd.DataFrame()

        fake, pro = _make_fake_tushare({"index_dailybasic": handler})
        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "tushare", fake)
            client = TushareIndexValuationClient(token="test-token")
            values = client.fetch_index_valuation("000300")

        assert len(values) == 3
        assert values[0].source == "tushare"
        # 按日期升序后：09-02 pe_ttm=12 为最早样本 → 中性 50；此后单调上升 → 百分位 100
        assert values[0].trade_date == date(2026, 9, 2)
        assert values[0].pe == 12.0  # pe_ttm 12.0，而非静态 pe 14.0
        assert values[0].pe_percentile == 50.0
        assert values[-1].pe == 14.0
        assert values[-1].pe_percentile == 100.0
        assert pro.calls["index_dailybasic"][0]["ts_code"] == "000300.SH"

    def test_not_configured_or_unsupported_returns_empty(self) -> None:
        """未配置 Token 或不支持的指数返回空列表。"""
        client = TushareIndexValuationClient(token="")
        assert client.fetch_index_valuation("000300") == []
        client2 = TushareIndexValuationClient(token="test")
        assert client2.fetch_index_valuation("000852") == []


class TestTushareMacroClient:
    """Tushare 宏观客户端。"""

    def test_fetch_cpi(self, monkeypatch) -> None:
        """cn_cpi 取全国同比 nt_yoy，period/period_date 标准化。"""
        df = pd.DataFrame(
            {
                "month": ["202607", "202606"],
                "nt_val": [100.5, 101.0],
                "nt_yoy": [0.5, 1.0],
            }
        )
        fake, _ = _make_fake_tushare({"cn_cpi": lambda **kw: df})
        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "tushare", fake)
            client = TushareMacroClient(token="test-token")
            rows = client.fetch_cpi_monthly()
        assert [r.period for r in rows] == ["2026-07", "2026-06"]
        assert rows[0].value == 0.5
        assert rows[0].period_date == "2026-07-01"
        assert rows[0].source == "tushare"

    def test_fetch_pmi(self, monkeypatch) -> None:
        """cn_pmi 取制造业 PMI010000 列。"""
        df = pd.DataFrame({"MONTH": ["202608"], "PMI010000": [49.8]})
        fake, _ = _make_fake_tushare({"cn_pmi": lambda **kw: df})
        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "tushare", fake)
            client = TushareMacroClient(token="test-token")
            rows = client.fetch_pmi()
        assert rows[0].period == "2026-08"
        assert rows[0].value == 49.8
        assert rows[0].indicator_code == "pmi"

    def test_fetch_lpr_tolerant_columns(self, monkeypatch) -> None:
        """shibor_lpr 兼容 date/1y/5y 列名。"""
        df = pd.DataFrame(
            {"date": ["20260820"], "1y": [3.0], "5y": [3.5]}
        )
        fake, _ = _make_fake_tushare({"shibor_lpr": lambda **kw: df})
        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "tushare", fake)
            client = TushareMacroClient(token="test-token")
            rows = client.fetch_lpr()
        codes = {row.indicator_code for row in rows}
        assert codes == {"lpr1y", "lpr5y"}
        by_code = {row.indicator_code: row.value for row in rows}
        assert by_code["lpr1y"] == 3.0
        assert by_code["lpr5y"] == 3.5


class TestTushareIndexMemberClient:
    """Tushare 指数成分客户端。"""

    def test_fetch_weight_snapshot_latest_month(self, monkeypatch) -> None:
        """只取不晚于目标日的最近月度快照，weight ÷100 归一化。"""
        df = pd.DataFrame(
            {
                "index_code": ["000300.SH"] * 4,
                "con_code": ["600000.SH", "000001.SZ", "600000.SH", "000001.SZ"],
                "trade_date": ["20260831", "20260831", "20260731", "20260731"],
                "weight": [3.5, 2.0, 3.0, 1.5],
            }
        )
        fake, pro = _make_fake_tushare({"index_weight": lambda **kw: df})
        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "tushare", fake)
            client = TushareIndexMemberClient(token="test-token")
            rows = client.fetch_weight_snapshot("000300", date(2026, 9, 8))

        assert pro.calls["index_weight"][0]["index_code"] == "000300.SH"
        by_code = {row["stock_code"]: row["weight"] for row in rows}
        assert by_code == {"600000": 0.035, "000001": 0.02}

    def test_fetch_weight_snapshot_csi_fallback(self, monkeypatch) -> None:
        """0 开头中证指数在 .SH 无数据时自动尝试 .CSI 后缀。"""

        def handler(**kwargs):
            ts_code = kwargs["index_code"]
            if ts_code == "000813.SH":
                return pd.DataFrame()
            return pd.DataFrame(
                {
                    "index_code": ["000813.CSI"],
                    "con_code": ["600519.SH"],
                    "trade_date": ["20260831"],
                    "weight": [4.0],
                }
            )

        fake, pro = _make_fake_tushare({"index_weight": handler})
        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "tushare", fake)
            client = TushareIndexMemberClient(token="test-token")
            rows = client.fetch_weight_snapshot("000813", date(2026, 9, 8))
        ts_codes = [call["index_code"] for call in pro.calls["index_weight"]]
        assert ts_codes == ["000813.SH", "000813.CSI"]
        assert rows == [{"stock_code": "600519", "weight": 0.04}]

    def test_unsupported_returns_empty(self) -> None:
        """未配置 Token 时直接返回空（由服务层降级）。"""
        client = TushareIndexMemberClient(token="")
        assert client.fetch_weight_snapshot("000300", date(2026, 9, 8)) == []


class TestTushareStockClient:
    """Tushare 个股数据客户端。"""

    def test_fetch_close_by_trade_date(self, monkeypatch) -> None:
        """全市场收盘返回代码/收盘价，覆盖沪深京后缀。"""
        df = pd.DataFrame(
            {
                "ts_code": ["600000.SH", "000001.SZ", "920000.BJ"],
                "close": [10.0, 11.0, 5.0],
            }
        )
        fake, _ = _make_fake_tushare({"daily": lambda **kw: df})
        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "tushare", fake)
            client = TushareStockClient(token="test-token")
            rows = client.fetch_close_by_trade_date(date(2026, 9, 8))
        assert [r["stock_code"] for r in rows] == ["600000", "000001", "920000"]
        assert rows[0]["source"] == "tushare"

    def test_fetch_history_close_sorted(self, monkeypatch) -> None:
        """单股历史收盘按日期升序，忽略缺失收盘。"""
        df = pd.DataFrame(
            {
                "ts_code": ["600000.SH"] * 3,
                "trade_date": ["20260908", "20260904", "20260903"],
                "close": [10.2, 10.0, None],
            }
        )
        fake, pro = _make_fake_tushare({"daily": lambda **kw: df})
        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "tushare", fake)
            client = TushareStockClient(token="test-token")
            rows = client.fetch_history_close("600000", "20260901", "20260908")
        assert pro.calls["daily"][0]["ts_code"] == "600000.SH"
        assert [r["trade_date"] for r in rows] == [
            date(2026, 9, 4),
            date(2026, 9, 8),
        ]
        assert [r["close"] for r in rows] == [10.0, 10.2]

    def test_fetch_stock_basics_active_and_delisted(self, monkeypatch) -> None:
        """上市名单 L 与退市名单 D 分别映射 is_active。"""

        def handler(**kwargs):
            if kwargs.get("list_status") == "L":
                return pd.DataFrame(
                    {
                        "ts_code": ["600000.SH", "920000.BJ"],
                        "name": ["浦发银行", "某北交所"],
                        "list_date": ["19991110", "20220301"],
                    }
                )
            return pd.DataFrame(
                {"ts_code": ["600001.SH"], "name": ["已退市股"], "list_date": [None]}
            )

        fake, _ = _make_fake_tushare({"stock_basic": handler})
        with monkeypatch.context() as mp:
            mp.setitem(sys.modules, "tushare", fake)
            client = TushareStockClient(token="test-token")
            rows = client.fetch_stock_basics()
        by_code = {row["stock_code"]: row for row in rows}
        assert by_code["600000"]["is_active"] is True
        assert by_code["600000"]["ipo_date"] == date(1999, 11, 10)
        assert by_code["600001"]["is_active"] is False
        assert all(row["source"] == "tushare" for row in rows)
