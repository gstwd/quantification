"""行业数据源管理（P03）纯函数与统计逻辑测试。"""

from __future__ import annotations

from datetime import date

import pytest

from quant_etf_api.domain.industry.bars import derive_prev_close_change
from quant_etf_api.domain.industry.membership import current_membership_counts
from quant_etf_api.domain.industry.quality import compute_industry_bar_quality


def _trading_days() -> list[date]:
    """构造含周末与节假日的样本交易日集合（2024-01-02 起共 10 个交易日）。"""
    return [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 9),
        date(2024, 1, 10),
        date(2024, 1, 11),
        date(2024, 1, 12),
        date(2024, 1, 15),
    ]


def test_quality_complete_history() -> None:
    """库内首日起完整覆盖到期望终点时缺失数为 0。"""
    actual = _trading_days()
    result = compute_industry_bar_quality(
        actual_dates=actual,
        trading_days=_trading_days(),
        expected_end=date(2024, 1, 15),
    )
    assert result == {
        "data_start_date": date(2024, 1, 2),
        "data_end_date": date(2024, 1, 15),
        "bar_count": 10,
        "missing_day_count": 0,
    }


def test_quality_counts_interior_and_tail_gap() -> None:
    """中间缺口与尾部滞后都按交易日历计入缺失。"""
    actual = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 8),
        date(2024, 1, 9),
        date(2024, 1, 10),
    ]
    result = compute_industry_bar_quality(
        actual_dates=actual,
        trading_days=_trading_days(),
        expected_end=date(2024, 1, 15),
    )
    # 1/5、1/11、1/12、1/15 共 4 个交易日缺失
    assert result["bar_count"] == 6
    assert result["missing_day_count"] == 4


def test_quality_no_data() -> None:
    """无任何日线时起止与缺失均为 None，条数为 0。"""
    result = compute_industry_bar_quality(
        actual_dates=[],
        trading_days=_trading_days(),
        expected_end=date(2024, 1, 15),
    )
    assert result == {
        "data_start_date": None,
        "data_end_date": None,
        "bar_count": 0,
        "missing_day_count": None,
    }


def test_quality_dirty_expected_end_before_first_bar() -> None:
    """期望终点早于库内首根日线时按无缺失处理。"""
    result = compute_industry_bar_quality(
        actual_dates=[date(2024, 2, 1), date(2024, 2, 2)],
        trading_days=_trading_days(),
        expected_end=date(2024, 1, 15),
    )
    assert result["bar_count"] == 2
    assert result["missing_day_count"] == 0


def test_derive_prev_close_change_first_row_none() -> None:
    """首行无前收时 prev_close/change_pct 为 None，后续行按上一 close 派生。"""
    rows = [
        {"trade_date": date(2024, 1, 2), "close_price": 100.0},
        {"trade_date": date(2024, 1, 3), "close_price": 110.0},
        {"trade_date": date(2024, 1, 4), "close_price": 99.0},
    ]
    result = derive_prev_close_change(rows)
    assert result[0]["prev_close_price"] is None
    assert result[0]["change_pct"] is None
    assert result[1]["prev_close_price"] == 100.0
    assert result[1]["change_pct"] == pytest.approx(10.0)
    assert result[2]["prev_close_price"] == 110.0
    assert result[2]["change_pct"] == pytest.approx(-10.0)


def test_derive_prev_close_change_override() -> None:
    """外部传入窗口前收盘时窗口首行正确派生涨跌幅。"""
    rows = [{"trade_date": date(2024, 1, 2), "close_price": 100.0}]
    result = derive_prev_close_change(rows, prev_close_override=90.0)
    assert result[0]["prev_close_price"] == 90.0
    assert round(result[0]["change_pct"], 4) == 11.1111


def test_derive_prev_close_change_single_row_without_override() -> None:
    """单行且无 override 时派生字段保持 None。"""
    rows = [{"trade_date": date(2024, 1, 2), "close_price": 100.0}]
    result = derive_prev_close_change(rows)
    assert result[0]["prev_close_price"] is None
    assert result[0]["change_pct"] is None


def test_membership_counts_dedupe_latest_and_filter_future() -> None:
    """同一股票取最新有效归属，未来生效事件不计入。"""
    events = [
        ("000001", "801780", date(2023, 1, 1)),
        ("000001", "801120", date(2024, 5, 1)),
        ("000001", "801150", date(2025, 1, 1)),  # 晚于 end_date，忽略
        ("000002", "801780", date(2022, 1, 1)),
        ("000003", "801080", date(2024, 6, 1)),
    ]
    counts = current_membership_counts(events, end_date=date(2024, 12, 31))
    assert counts == {"801120": 1, "801780": 1, "801080": 1}
