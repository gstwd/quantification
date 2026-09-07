"""个股收盘全市场快照源回退测试（东财主源失败时切新浪备用源）。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from quant_etf_api.infra.clients import stock_close_client as client_module
from quant_etf_api.infra.clients.stock_close_client import StockCloseClient


def _spot_df(codes: list[str], prices: list[float]) -> pd.DataFrame:
    """构造“代码/最新价”两列的 AkShare 快照返回。"""
    return pd.DataFrame({"代码": codes, "最新价": prices})


class TestSpotSnapshotFallback:
    """全市场个股快照的东财→新浪回退逻辑。"""

    def test_primary_source_used_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """东财主源可用时应直接使用，不回退新浪。"""
        monkeypatch.setattr(
            client_module.ak,
            "stock_zh_a_spot_em",
            lambda: _spot_df(["000001", "600000"], [10.5, 8.8]),
        )

        def unexpected_call() -> None:
            raise AssertionError("主源可用时不应调用新浪备用源")

        monkeypatch.setattr(client_module.ak, "stock_zh_a_spot", unexpected_call)

        rows = StockCloseClient().fetch_all_close_snapshot()

        assert len(rows) == 2
        assert rows[0]["stock_code"] == "000001"
        assert rows[0]["close"] == 10.5
        assert rows[0]["source"] == "akshare_em"
        assert rows[0]["trade_date"] == date.today()

    def test_fallback_to_sina_when_primary_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """东财接口抛代理异常时应回退新浪，并兼容带交易所前缀的代码。"""

        def primary_fail() -> None:
            raise RuntimeError("push2 东财代理不可达")

        monkeypatch.setattr(client_module.ak, "stock_zh_a_spot_em", primary_fail)
        monkeypatch.setattr(
            client_module.ak,
            "stock_zh_a_spot",
            lambda: _spot_df(["sh600000", "sz000001", "bj430047"], [8.8, 10.5, 3.2]),
        )

        rows = StockCloseClient().fetch_all_close_snapshot()

        assert {row["stock_code"] for row in rows} == {"600000", "000001", "430047"}
        assert all(row["source"] == "akshare_sina" for row in rows)
        assert {row["close"] for row in rows} == {8.8, 10.5, 3.2}

    def test_raises_when_all_sources_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """全部源均抛异常时应抛出汇总错误的 RuntimeError。"""
        monkeypatch.setattr(
            client_module.ak,
            "stock_zh_a_spot_em",
            lambda: (_ for _ in ()).throw(RuntimeError("东财失败")),
        )
        monkeypatch.setattr(
            client_module.ak,
            "stock_zh_a_spot",
            lambda: (_ for _ in ()).throw(RuntimeError("新浪失败")),
        )

        with pytest.raises(RuntimeError, match="stock_zh_a_spot_em.*stock_zh_a_spot"):
            StockCloseClient().fetch_all_close_snapshot()

    def test_raises_when_all_sources_return_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """全部源返回空 DataFrame 时应按无数据报错而非静默成功。"""
        monkeypatch.setattr(
            client_module.ak,
            "stock_zh_a_spot_em",
            lambda: _spot_df([], []),
        )
        monkeypatch.setattr(
            client_module.ak,
            "stock_zh_a_spot",
            lambda: _spot_df([], []),
        )

        with pytest.raises(RuntimeError, match="返回空数据"):
            StockCloseClient().fetch_all_close_snapshot()
