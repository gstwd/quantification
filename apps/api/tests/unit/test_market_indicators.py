"""日频市场指标与宏观扩充测试：序列因子、ERP 利率来源、基差计算、同步编排与两融归一化。"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pandas as pd
import pytest

from quant_etf_api.domain.market_data.indicators import (
    MARKET_INDICATORS,
    MarketIndicatorPoint,
    codes_of_group,
)
from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.erp import ERPComputer
from quant_etf_api.factors.builtins.series import MacroSeriesComputer, MarketSeriesComputer
from quant_etf_api.factors.macro_period import macro_available_date
from quant_etf_api.infra.clients.market_indicator_client import TushareMarketIndicatorClient
from quant_etf_api.services.market_indicator_service import MarketIndicatorService

_D = date(2025, 3, 3)


class TestSeriesFactors:
    """宏观 / 市场序列因子。"""

    def test_macro_release_lag(self) -> None:
        """M1 同比次月 20 日才可见。"""
        assert macro_available_date("m1_yoy", "2025-01") == date(2025, 2, 20)
        ctx = FactorContext(macro_indicators={"m1_yoy": {"2025-01-01": 2.0, "2025-02-01": 3.0}})
        computer = MacroSeriesComputer("m1_yoy", "level", 1)
        values = computer.compute_batch("X", [date(2025, 3, 19), date(2025, 3, 20)], ctx)
        assert values[date(2025, 3, 19)].numeric == 2.0
        assert values[date(2025, 3, 20)].numeric == 3.0

    def test_macro_pct_change_yoy(self) -> None:
        """社融存量同比：pct_change 12 个月。"""
        data = {f"{2024 + (m // 12)}-{m % 12 + 1:02d}-01": 400.0 + m for m in range(14)}
        ctx = FactorContext(macro_indicators={"sf_stock": data})
        value = MacroSeriesComputer("sf_stock", "pct_change", 12).compute(
            "X", date(2025, 4, 1), ctx
        )
        # 最新可见 2025-02（413）对 2024-02（401）
        assert value.numeric == pytest.approx((413 / 401 - 1) * 100, abs=1e-6)

    def test_market_window_and_staleness(self) -> None:
        """日频：窗口均值、变化率；最新观测陈旧时缺失。"""
        days = [_D + timedelta(days=i) for i in range(5)]
        ctx = FactorContext(
            macro_indicators={"margin_rzye": {d.isoformat(): 100.0 + i for i, d in enumerate(days)}}
        )
        assert MarketSeriesComputer("margin_rzye", "level", 2).compute(
            "X", days[-1], ctx
        ).numeric == pytest.approx(103.5)
        assert MarketSeriesComputer("margin_rzye", "pct_change", 4).compute(
            "X", days[-1], ctx
        ).numeric == pytest.approx(4.0)
        stale = days[-1] + timedelta(days=30)
        assert MarketSeriesComputer("margin_rzye", "level", 2).compute("X", stale, ctx).numeric is None


class TestErpRateSource:
    """ERP 无风险利率来源。"""

    def _ctx(self) -> FactorContext:
        return FactorContext(
            index_valuation={("000300", _D): SimpleNamespace(pe=12.5)},
            macro_indicators={"lpr1y": {"2025-02-20": 3.1}, "cgb10y": {_D.isoformat(): 1.8}},
        )

    def test_default_lpr_payload_unchanged(self) -> None:
        """缺省 LPR：数值与 payload 键与改造前一致。"""
        value = ERPComputer().compute("000300", _D, self._ctx())
        assert value.numeric == pytest.approx(8.0 - 3.1)
        assert set(value.payload) >= {"lpr_1y", "lpr_period"}

    def test_cgb10y(self) -> None:
        """10 年国债口径。"""
        value = ERPComputer("cgb10y").compute("000300", _D, self._ctx())
        assert value.numeric == pytest.approx(8.0 - 1.8)
        assert value.payload["rate_source"] == "cgb10y"


class TestMarketIndicatorService:
    """同步编排与基差。"""

    def _service(self) -> MarketIndicatorService:
        tushare = mock.MagicMock()
        akshare = mock.MagicMock()
        svc = MarketIndicatorService(mock.MagicMock(), tushare_client=tushare, akshare_client=akshare)
        svc._repo = mock.MagicMock()
        svc._repo.upsert.side_effect = lambda points, overwrite: len(list(points))
        return svc

    def test_basis_from_spot_closes(self) -> None:
        """基差 = 期货 / 现货 − 1；年化 = 基差 × 365 / 剩余天数。"""
        svc = self._service()
        fetched = [
            MarketIndicatorPoint("fut_if_close", _D, 3960.0, "tushare"),
            MarketIndicatorPoint("fut_if_dte", _D, 73.0, "tushare"),
        ]
        bars = [SimpleNamespace(trade_date=_D, close_price=4000.0)]
        with mock.patch(
            "quant_etf_api.services.market_indicator_service.IndexDailyBarRepository"
        ) as repo:
            repo.return_value.find_by_code_date_range.return_value = bars
            points = svc._basis_points("if", fetched, _D, _D)
        by_code = {p.indicator_code: p.value for p in points}
        assert by_code["fut_if_basis"] == pytest.approx(-1.0)
        assert by_code["fut_if_basis_ann"] == pytest.approx(-5.0)

    def test_group_failure_isolated(self) -> None:
        """单个分组失败不影响其他分组，失败分组记入 errors。"""
        svc = self._service()
        svc._repo.latest_dates.return_value = {}
        svc._akshare.fetch_cgb10y.side_effect = RuntimeError("down")
        svc._tushare.fetch_margin.return_value = [
            MarketIndicatorPoint("margin_rzye", _D, 1.0, "tushare")
        ]
        result = svc.sync(_D, partition_key=None, from_epoch=True)
        assert result["errors"][0].startswith("cgb10y:")
        assert result["records"] >= 1
        assert result["sources"]["tushare"] >= 1

    def test_incremental_start(self) -> None:
        """增量同步从分组内最旧的最新日次日拉起。"""
        svc = self._service()
        svc._repo.latest_dates.return_value = {
            "margin_rzye": date(2025, 3, 1),
            "margin_rzmre": date(2025, 2, 27),
        }
        assert svc._start_of("margin", from_epoch=False) == date(2025, 2, 28)

    def test_unknown_partition_rejected(self) -> None:
        """未知指标代码被拒绝。"""
        with pytest.raises(ValueError):
            self._service().sync(_D, partition_key="nope")


class TestClientNormalization:
    """Tushare 两融汇总归一化。"""

    def test_margin_requires_both_exchanges(self) -> None:
        """沪深两市都有数据才出值，单位换算为亿元。"""
        client = TushareMarketIndicatorClient(token="x")
        frame = pd.DataFrame(
            [
                {"trade_date": "20250303", "exchange_id": "SSE", "rzye": 1e12, "rzmre": 5e10, "rqye": 1e9},
                {"trade_date": "20250303", "exchange_id": "SZSE", "rzye": 1e12, "rzmre": 5e10, "rqye": 1e9},
                {"trade_date": "20250304", "exchange_id": "SSE", "rzye": 1e12, "rzmre": 5e10, "rqye": 1e9},
            ]
        )
        client._fetch = mock.MagicMock(return_value=frame)  # type: ignore[method-assign]
        points = client.fetch_margin(date(2025, 3, 3), date(2025, 3, 4))
        values: dict[str, Any] = {p.indicator_code: p.value for p in points}
        assert {p.trade_date for p in points} == {date(2025, 3, 3)}
        assert values["margin_rzye"] == pytest.approx(20000.0)

    def test_indicator_table_groups(self) -> None:
        """代码表分组完整。"""
        assert set(codes_of_group("margin")) == {"margin_rzye", "margin_rzmre", "margin_rqye"}
        assert "fut_im_basis_ann" in MARKET_INDICATORS
        assert len(codes_of_group("options")) == 6
