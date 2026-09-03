"""测试多数据源共享的日线结构、OHLC 严格校验与跨源一致性对比助手。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant_etf_api.infra.clients.index_daily_common import (
    IndexDailyBar,
    _build_index_bars,
    incremental_start_date,
    _index_code_to_market_symbol,
    _parse_bar_date,
    compare_index_bar_overlap,
    index_code_market_prefix,
    is_ohlc_complete,
    ohlc_missing_count,
)


def _bars(
    ohlc: list[tuple[float | None, float | None, float | None, float | None]],
) -> list[IndexDailyBar]:
    """按 (open, high, low, close) 构造日线列表，其余字段补默认值。"""
    return [
        IndexDailyBar(
            trade_date=date(2026, 1, 1),
            open_price=o,
            close_price=c,
            high_price=h,
            low_price=low,
            volume=0.0,
            turnover=0.0,
        )
        for o, h, low, c in ohlc
    ]


class TestMarketPrefix:
    """指数代码 → 交易所前缀/市场代码映射。"""

    def test_sh_index(self) -> None:
        """上证/中证系指数归属沪市。"""
        assert index_code_market_prefix("000300") == "sh"
        assert index_code_market_prefix("000016") == "sh"
        assert index_code_market_prefix("931743") == "sh"
        assert _index_code_to_market_symbol("000300") == "sh000300"

    def test_h_series_csi_index(self) -> None:
        """H 开头中证策略指数归属沪市（如 H30269 红利低波）。"""
        assert index_code_market_prefix("H30269") == "sh"
        assert _index_code_to_market_symbol("H30269") == "shH30269"

    def test_sz_index(self) -> None:
        """深证系指数归属深市。"""
        assert index_code_market_prefix("399001") == "sz"
        assert index_code_market_prefix("399006") == "sz"
        assert _index_code_to_market_symbol("399006") == "sz399006"


class TestParseBarDate:
    """上游日期统一解析。"""

    def test_str_datetime_timestamp(self) -> None:
        """字符串、datetime、date 均能解析为 date。"""
        assert _parse_bar_date("2026-01-05") == date(2026, 1, 5)
        from datetime import datetime

        assert _parse_bar_date(datetime(2026, 1, 5, 15, 0)) == date(2026, 1, 5)
        assert _parse_bar_date(date(2026, 1, 5)) == date(2026, 1, 5)
        assert _parse_bar_date(pd.Timestamp("2026-01-05")) == date(2026, 1, 5)


class TestIncrementalStartDate:
    """增量缓冲窗口回退。"""

    def test_buffer_days(self) -> None:
        """起点回退 10 个自然日。"""
        assert incremental_start_date(date(2026, 1, 15)) == date(2026, 1, 5)


class TestStrictOhlcQuality:
    """OHLC 严格校验（任一缺失即不合格）。"""

    def test_complete(self) -> None:
        """OHLC 四价完整时缺失数为 0。"""
        bars = _bars([(100.0, 101.0, 99.0, 100.5), (101.0, 102.0, 100.0, 101.5)])
        assert ohlc_missing_count(bars) == 0
        assert is_ohlc_complete(bars) is True

    def test_any_single_missing_is_unqualified(self) -> None:
        """只要有一项缺失（含收盘价）即视为不合格。"""
        bars = _bars(
            [
                (100.0, 101.0, 99.0, 100.5),
                (None, 101.0, 99.0, 100.5),  # 开盘缺失
                (100.0, float("nan"), 99.0, 100.5),  # 最高缺失
                (100.0, 101.0, 0.0, 100.5),  # 最低非正
                (100.0, 101.0, 99.0, None),  # 收盘缺失
            ]
        )
        assert ohlc_missing_count(bars) == 4
        assert is_ohlc_complete(bars) is False

    def test_empty_is_complete(self) -> None:
        """空列表无缺失可判定，视为完整。"""
        assert is_ohlc_complete([]) is True
        assert ohlc_missing_count([]) == 0


class TestBuildIndexBarsRobust:
    """DataFrame → 日线转换的健壮性（空字符串缺失值）。"""

    def test_empty_string_missing_becomes_nan(self) -> None:
        """baostock 空字符串缺失应转为 NaN 并由严格校验判为不合格。"""
        df = pd.DataFrame(
            {
                "date": ["2026-01-05", "2026-01-06"],
                "open": ["4661.61", "4719.21"],
                "close": ["4717.75", "4790.69"],
                "high": ["4721.64", ""],
                "low": ["4661.62", "4718.01"],
                "volume": ["23414483900", "29697068600"],
                "amount": ["630577351311", "725414566872"],
            }
        )
        bars = _build_index_bars(
            df,
            "date",
            "open",
            "close",
            "high",
            "low",
            volume_col="volume",
            amount_col="amount",
        )
        assert len(bars) == 2
        assert ohlc_missing_count(bars) == 1


class TestCompareIndexBarOverlap:
    """跨源共同交易日收盘点位一致性对比。"""

    @staticmethod
    def _bars_with_close(dates: list[str], closes: list[float]) -> list[IndexDailyBar]:
        """按日期和收盘价构造日线列表。"""
        return [
            IndexDailyBar(
                trade_date=date.fromisoformat(d),
                open_price=c,
                close_price=c,
                high_price=c,
                low_price=c,
                volume=0.0,
                turnover=0.0,
            )
            for d, c in zip(dates, closes)
        ]

    def test_overlap_max_diff(self) -> None:
        """共同日收盘点位最大差与共同天数计算正确。"""
        a = self._bars_with_close(["2026-01-05", "2026-01-06", "2026-01-07"], [100.0, 101.0, 102.0])
        b = self._bars_with_close(["2026-01-06", "2026-01-07", "2026-01-08"], [101.5, 102.0, 103.0])
        common, max_diff = compare_index_bar_overlap(a, b)
        assert common == 2
        assert max_diff == 0.5

    def test_no_overlap(self) -> None:
        """无共同交易日时返回 (0, None)。"""
        a = self._bars_with_close(["2026-01-05"], [100.0])
        b = self._bars_with_close(["2026-01-06"], [101.0])
        assert compare_index_bar_overlap(a, b) == (0, None)
