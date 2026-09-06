"""行业因子服务调试元信息路径测试（不依赖真库）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from quant_etf_api.services.industry_factor_service import IndustryFactorService


def _event(stock: str, industry: str, start: date) -> SimpleNamespace:
    """构造成分事件对象。"""
    return SimpleNamespace(
        stock_code=stock,
        industry_code=industry,
        start_date=start,
    )


class _FakeCloseRepo:
    """只实现 find_close_rows 的假个股收盘仓库。"""

    def __init__(self, rows: list[tuple[date, str, float]]) -> None:
        self.rows = rows

    def find_close_rows(
        self,
        start: date,
        end: date,
        stock_codes: list[str],
    ) -> list[tuple[date, str, float]]:
        """按日期与股票过滤收盘行。"""
        codes = set(stock_codes)
        return [
            row
            for row in self.rows
            if start <= row[0] <= end and row[1] in codes
        ]


class _FakeMembershipRepo:
    """只实现 find_events_until 的假成分仓库。"""

    def __init__(self, events: list[SimpleNamespace]) -> None:
        self.events = events

    def find_events_until(self, end: date) -> list[SimpleNamespace]:
        """返回全部事件（测试中均已生效）。"""
        return [e for e in self.events if e.start_date <= end]


class _FakeUniverseRepo:
    """只实现行业目录查询的假仓库。"""

    def __init__(self, codes: list[str]) -> None:
        self.codes = codes

    def find_active_codes(self, codes: list[str] | None = None) -> list[str]:
        """返回启用行业代码。"""
        if codes is None:
            return self.codes
        return [code for code in self.codes if code in codes]


class _FakeBarRepo:
    """只返回行业日线行的假仓库。"""

    def __init__(self, rows: list[tuple[date, str, float]]) -> None:
        self.rows = rows

    def find_range(
        self,
        start: date,
        end: date,
        industry_codes: list[str] | None = None,
    ) -> list[SimpleNamespace]:
        """返回区间内行业日线行。"""
        codes = set(industry_codes or [])
        return [
            SimpleNamespace(
                trade_date=row[0],
                industry_code=row[1],
                close_price=row[2],
            )
            for row in self.rows
            if start <= row[0] <= end and (not codes or row[1] in codes)
        ]


def _build_service(
    close_rows: list[tuple[date, str, float]],
    events: list[SimpleNamespace],
) -> IndustryFactorService:
    """绕过 __init__ 组装带假仓库的因子服务实例。"""
    service = object.__new__(IndustryFactorService)
    service._db = MagicMock()
    service._close_repo = _FakeCloseRepo(close_rows)
    service._membership_repo = _FakeMembershipRepo(events)
    return service


def _build_panel_service(
    bar_rows: list[tuple[date, str, float]],
) -> IndustryFactorService:
    """组装仅含行业日线/目录假仓库的因子服务实例。"""
    service = object.__new__(IndustryFactorService)
    service._db = MagicMock()
    service._universe_repo = _FakeUniverseRepo(["801010"])
    service._bar_repo = _FakeBarRepo(bar_rows)
    return service


def test_build_panels_slices_empty_panels_with_timestamp() -> None:
    """空面板切片用 Timestamp 与 datetime64[s] 索引比较（pandas 3 回归）。"""
    start = date(2024, 1, 2)
    end = date(2024, 1, 4)
    rows = [
        (start, "801010", 100.0),
        (date(2024, 1, 3), "801010", 101.0),
        (end, "801010", 102.0),
    ]
    service = _build_panel_service(rows)
    panels = service.build_panels(
        start=start,
        end=end,
        industry_codes=["801010"],
        need_rrg=False,
        need_diffusion=False,
    )
    assert list(panels["industry_codes"]) == ["801010"]
    assert panels["rs_ratio"].isna().all().all()
    assert panels["diffusion"].isna().all().all()


def test_diffusion_panel_built_per_industry_with_stats() -> None:
    """单行业分批构建返回扩散宽表、逐日统计与覆盖项。"""
    trading_dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    ]
    rows = [
        (trading_dates[0], "000001", 10.0),
        (trading_dates[1], "000001", 11.0),
        (trading_dates[2], "000001", 12.0),
        (trading_dates[3], "000001", 13.0),
        (trading_dates[0], "000002", 10.0),
        (trading_dates[1], "000002", 10.0),
        (trading_dates[2], "000002", 11.0),
        (trading_dates[3], "000002", 12.0),
    ]
    events = [
        _event("000001", "801010", date(2014, 2, 21)),
        _event("000002", "801010", date(2014, 2, 21)),
    ]
    service = _build_service(rows, events)
    frame, extra = service._build_diffusion_panel_with_stats(
        start=trading_dates[0],
        end=trading_dates[-1],
        codes=["801010"],
        lookback=1,
        smooth_window=1,
        market_dates=trading_dates,
        collect_daily=True,
    )
    assert list(frame.columns) == ["801010"]
    # 首日无 t-1 收盘 → NaN；其后 0.5 / 1.0 / 1.0
    assert np.isnan(frame["801010"].iloc[0])
    assert frame["801010"].iloc[1] == 0.5
    assert frame["801010"].iloc[2] == 1.0
    assert frame["801010"].iloc[3] == 1.0

    daily = extra["daily"]
    assert daily["member"] is not None and daily["valid"] is not None
    assert daily["member"]["801010"].iloc[0] == 2
    assert daily["valid"]["801010"].iloc[0] == 0
    item = extra["items"][0]
    assert item["member_count"] == 2
    assert item["no_sample_days"] == 1
    assert item["valid_count"] == 3
    assert item["valid_from"] == trading_dates[1]
    assert item["valid_until"] == trading_dates[-1]


def test_diffusion_panel_reports_missing_membership_issue() -> None:
    """所选行业均无成分事件时给出明确报错（对应 API 422 提示）。"""
    trading_dates = [date(2024, 1, 2), date(2024, 1, 3)]
    events = [_event("000001", "801010", date(2014, 2, 21))]
    service = _build_service([], events)
    with pytest.raises(ValueError, match="均无成分事件"):
        service._build_diffusion_panel_with_stats(
            start=trading_dates[0],
            end=trading_dates[-1],
            codes=["801780"],
            lookback=1,
            smooth_window=1,
            market_dates=trading_dates,
            collect_daily=True,
        )
