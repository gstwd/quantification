"""测试交易日历的 Tushare 优先加载与 AkShare 兜底（严格口径 C1）。"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import quant_etf_api.infra.trading_calendar as calendar_module
from quant_etf_api.domain.common.trading_calendar import TradingCalendarUnavailableError


def _full_calendar(last_year: int = 2026, *, skip_year: int | None = None) -> set[date]:
    """构造 1991 年起每年约 246 个"交易日"的完整日历（剔除每年前 15 个工作日）。"""
    days: set[date] = set()
    for year in range(1991, last_year + 1):
        if year == skip_year:
            continue
        weekdays = [
            date(year, 1, 1) + timedelta(days=offset)
            for offset in range((date(year + 1, 1, 1) - date(year, 1, 1)).days)
            if (date(year, 1, 1) + timedelta(days=offset)).weekday() < 5
        ]
        days.update(weekdays[15:])
    return days


def test_upstream_falls_back_to_akshare_when_no_token(monkeypatch) -> None:
    """未配置 Token 时直接使用 AkShare 兜底，并标明来源。"""
    monkeypatch.setattr(
        calendar_module,
        "get_settings",
        lambda: SimpleNamespace(tushare_token=""),
    )
    ak_days = _full_calendar()
    monkeypatch.setattr(calendar_module, "_load_from_akshare", lambda: ak_days)
    monkeypatch.setattr(calendar_module, "_load_from_tushare", lambda: None)
    assert calendar_module._load_from_upstream() == (ak_days, "akshare")


def test_upstream_prefers_tushare_when_configured(monkeypatch) -> None:
    """配置 Token 后优先使用 Tushare 返回的交易日。"""
    ts_days = _full_calendar()
    ak_days = _full_calendar() - {date(2026, 9, 1)}
    monkeypatch.setattr(calendar_module, "_load_from_tushare", lambda: ts_days)
    monkeypatch.setattr(calendar_module, "_load_from_akshare", lambda: ak_days)
    assert calendar_module._load_from_upstream() == (ts_days, "tushare")


def test_upstream_returns_none_when_both_fail(monkeypatch) -> None:
    """上游两个数据源都失败时返回 (None, None)——不再降级为周末判断（C1）。"""
    monkeypatch.setattr(calendar_module, "_load_from_tushare", lambda: None)
    monkeypatch.setattr(calendar_module, "_load_from_akshare", lambda: None)
    assert calendar_module._load_from_upstream() == (None, None)


def test_tushare_loader_parses_paged_calendar(monkeypatch) -> None:
    """trade_cal 分页返回按 5 年窗口解析为交易日集合。"""

    def handler(**kwargs):
        start = str(kwargs["start_date"])
        assert int(start[:4]) % 5 == 0
        return __import__("pandas").DataFrame(
            {"cal_date": [f"{start[:4]}0101", f"{start[:4]}0102"]}
        )

    class _FakePro:
        def trade_cal(self, **kwargs):
            """返回两个日期。"""
            return handler(**kwargs)

    class _FakeTushare:
        def set_token(self, token: str) -> None:
            """记录 Token。"""

        def pro_api(self):
            """返回假 pro。"""
            return _FakePro()

    monkeypatch.setattr(
        calendar_module,
        "get_settings",
        lambda: SimpleNamespace(tushare_token="test-token"),
    )
    monkeypatch.setitem(sys.modules, "tushare", _FakeTushare())
    days = calendar_module._load_from_tushare()
    # 1990 起按 5 年窗口分页，最后一个窗口为 2025-2026（当前年份）
    assert date(1990, 1, 1) in days
    assert date(2025, 1, 1) in days
    assert date(2025, 1, 2) in days
    assert len(days) == 16


def test_completeness_rejects_calendar_with_missing_year() -> None:
    """任一完整年度缺失（例如某个 5 年分段为空）时拒绝该日历。"""
    calendar_module.validate_calendar_completeness(_full_calendar(), today=date(2026, 10, 11))
    with pytest.raises(TradingCalendarUnavailableError, match="2003=0"):
        calendar_module.validate_calendar_completeness(
            _full_calendar(skip_year=2003), today=date(2026, 10, 11)
        )


def test_upstream_skips_incomplete_tushare_calendar(monkeypatch) -> None:
    """Tushare 日历不完整时不采用，回退到通过校验的 AkShare 日历。"""
    ak_days = _full_calendar()
    monkeypatch.setattr(
        calendar_module, "_load_from_tushare", lambda: _full_calendar(skip_year=2010)
    )
    monkeypatch.setattr(calendar_module, "_load_from_akshare", lambda: ak_days)
    assert calendar_module._load_from_upstream() == (ak_days, "akshare")


def test_tushare_loader_fails_when_any_page_is_empty(monkeypatch) -> None:
    """trade_cal 任一 5 年分段返回空时整体判为失败，而不是跳过该分段。"""
    import pandas as pd

    class _FakePro:
        def trade_cal(self, **kwargs):
            """2000 年分段返回空，其余分段返回一个日期。"""
            start = str(kwargs["start_date"])
            if start.startswith("2000"):
                return pd.DataFrame({"cal_date": []})
            return pd.DataFrame({"cal_date": [f"{start[:4]}0104"]})

    class _FakeTushare:
        def set_token(self, token: str) -> None:
            """记录 Token。"""

        def pro_api(self) -> _FakePro:
            """返回假的 pro 接口。"""
            return _FakePro()

    monkeypatch.setattr(
        calendar_module, "get_settings", lambda: SimpleNamespace(tushare_token="token")
    )
    monkeypatch.setitem(sys.modules, "tushare", _FakeTushare())
    monkeypatch.setattr(calendar_module.time, "sleep", lambda _seconds: None)
    assert calendar_module._load_from_tushare() is None
