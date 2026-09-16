"""行业数据源管理（P03）纯函数与统计逻辑测试。

说明：行业日线质量规则（`compute_industry_bar_quality`）已随质量口径统一
删除——缺口/异常统计由 `DataManagementService` 在数据库层按数据集实现
（与 `data_health_snapshot` 同源），相关用例一并移除。
"""

from __future__ import annotations

from datetime import date

import pytest

from quant_etf_api.domain.industry.bars import derive_prev_close_change
from quant_etf_api.domain.industry.membership import current_membership_counts


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
