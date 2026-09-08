"""测试交易日历的 Tushare 优先加载与 AkShare 兜底。"""

from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace

import quant_etf_api.infra.trading_calendar as calendar_module


def test_upstream_falls_back_to_akshare_when_no_token(monkeypatch) -> None:
    """未配置 Token 时直接使用 AkShare 兜底。"""
    monkeypatch.setattr(
        calendar_module,
        "get_settings",
        lambda: SimpleNamespace(tushare_token=""),
    )
    ak_days = {date(2026, 9, 7), date(2026, 9, 8)}
    monkeypatch.setattr(calendar_module, "_load_from_akshare", lambda: ak_days)
    monkeypatch.setattr(calendar_module, "_load_from_tushare", lambda: None)
    assert calendar_module._load_from_upstream() == ak_days


def test_upstream_prefers_tushare_when_configured(monkeypatch) -> None:
    """配置 Token 后优先使用 Tushare 返回的交易日。"""
    ts_days = {date(2026, 9, 7), date(2026, 9, 8)}
    ak_days = {date(2026, 9, 1)}
    monkeypatch.setattr(calendar_module, "_load_from_tushare", lambda: ts_days)
    monkeypatch.setattr(calendar_module, "_load_from_akshare", lambda: ak_days)
    assert calendar_module._load_from_upstream() == ts_days


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
