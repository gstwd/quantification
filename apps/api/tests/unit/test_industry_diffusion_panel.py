"""扩散按行业分批计算的纯函数与缺失规则测试。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np

from quant_etf_api.domain.industry.diffusion_panel import (
    build_membership_chains,
    industry_diffusion_series,
    member_stock_codes,
)


def _event(stock: str, industry: str, start: date) -> SimpleNamespace:
    """构造成分事件测试对象。"""
    return SimpleNamespace(
        stock_code=stock,
        industry_code=industry,
        start_date=start,
    )


def test_build_membership_chains_sorted_and_member_filter() -> None:
    """归属链按生效日升序，成员集包含曾迁出股票。"""
    events = [
        _event("600000", "801780", date(2014, 2, 21)),
        _event("600000", "801230", date(2023, 1, 1)),
        _event("000001", "801010", date(2020, 6, 1)),
    ]
    chains = build_membership_chains(events)
    assert chains["600000"] == [
        (date(2014, 2, 21), "801780"),
        (date(2023, 1, 1), "801230"),
    ]
    assert member_stock_codes(chains, "801780") == ["600000"]
    assert member_stock_codes(chains, "801010") == ["000001"]


def test_industry_diffusion_missing_prev_close_excluded() -> None:
    """两日收盘任一缺失的股票不计入有效样本，分母随之收缩。"""
    trading_dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    ]
    chains = build_membership_chains(
        [_event("000001", "801010", date(2014, 2, 21))]
    )
    close_rows = [
        (trading_dates[0], "000001", 10.0),
        (trading_dates[1], "000001", 11.0),
        (trading_dates[2], "000001", 10.5),
        (trading_dates[3], "000001", 12.0),
    ]
    series, stats = industry_diffusion_series(
        close_rows,
        trading_dates,
        chains,
        "801010",
        lookback=1,
        smooth_window=1,
    )
    # 第 1 日无 t-1 收盘 → 不计样本（raw NaN）；第 2/3/4 日判涨 1/0/1
    assert np.isnan(stats["raw_ratio"].iloc[0])
    assert stats["valid_count"].iloc[0] == 0
    assert stats["member_count"].iloc[0] == 1
    assert stats["raw_ratio"].iloc[1] == 1.0
    assert stats["raw_ratio"].iloc[2] == 0.0
    assert stats["raw_ratio"].iloc[3] == 1.0
    assert np.isnan(series.iloc[0])


def test_industry_diffusion_moved_stock_excluded_after_switch() -> None:
    """股票迁出行业后不再进入该行业的成员/分母统计。"""
    trading_dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
    ]
    chains = build_membership_chains(
        [
            _event("600000", "801780", date(2014, 2, 21)),
            _event("600000", "801230", date(2024, 1, 3)),
        ]
    )
    close_rows = [
        (trading_dates[0], "600000", 10.0),
        (trading_dates[1], "600000", 11.0),
        (trading_dates[2], "600000", 12.0),
    ]
    _series, stats = industry_diffusion_series(
        close_rows,
        trading_dates,
        chains,
        "801780",
        lookback=1,
        smooth_window=1,
    )
    assert stats["member_count"].iloc[0] == 1
    assert stats["member_count"].iloc[1] == 0
    assert stats["member_count"].iloc[2] == 0


def test_industry_diffusion_no_members_all_nan() -> None:
    """无成分事件的行业返回全 NaN 序列与零成员统计。"""
    trading_dates = [date(2024, 1, 2), date(2024, 1, 3)]
    chains = build_membership_chains([_event("000001", "801010", date(2014, 2, 21))])
    series, stats = industry_diffusion_series(
        [],
        trading_dates,
        chains,
        "801780",
        lookback=1,
        smooth_window=1,
    )
    assert series.isna().all()
    assert (stats["member_count"] == 0).all()
    assert (stats["valid_count"] == 0).all()


def test_industry_diffusion_rolling_nan_poisons_window() -> None:
    """smooth 窗口内任意 NaN 使当日平滑值为 NaN（研报不补足口径）。"""
    trading_dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    ]
    chains = build_membership_chains(
        [_event("000001", "801010", date(2014, 2, 21))]
    )
    close_rows = [
        (trading_dates[0], "000001", 10.0),
        (trading_dates[1], "000001", 11.0),
        (trading_dates[2], "000001", 12.0),
        (trading_dates[3], "000001", 13.0),
        (trading_dates[4], "000001", 14.0),
    ]
    series, _stats = industry_diffusion_series(
        close_rows,
        trading_dates,
        chains,
        "801010",
        lookback=1,
        smooth_window=3,
    )
    # 第 1 日 raw 为 NaN 会污染窗口：前三日平滑均为 NaN；第 4~5 日才为 1.0
    assert np.isnan(series.iloc[0])
    assert np.isnan(series.iloc[1])
    assert np.isnan(series.iloc[2])
    assert series.iloc[3] == 1.0
    assert series.iloc[4] == 1.0
