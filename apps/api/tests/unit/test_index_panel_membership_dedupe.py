"""测试指数成分面板按股票去重（PIT 与当前快照重叠场景）。"""

from __future__ import annotations

from datetime import date

from quant_etf_api.services.index_factor_panel_service import (
    _merge_members_by_stock,
)


def _member(
    code: str,
    start: date,
    snapshot_type: str = "pit",
) -> dict:
    """构造单条成分事件字典。"""
    return {
        "stock_code": code,
        "weight": 0.01,
        "start_date": start,
        "snapshot_type": snapshot_type,
    }


def test_latest_start_wins() -> None:
    """同一股票出现两个月度 PIT 时保留较新的 start_date。"""
    members = [
        _member("600000", date(2026, 7, 31)),
        _member("600000", date(2026, 8, 31)),
        _member("000001", date(2026, 8, 31)),
    ]
    result = _merge_members_by_stock(members)
    by_code = {row["stock_code"]: row for row in result}
    assert by_code["600000"]["start_date"] == date(2026, 8, 31)
    assert len(result) == 2


def test_current_snapshot_wins_same_start() -> None:
    """同日同时存在 PIT 与当前快照时优先当前快照，避免重复计数。"""
    members = [
        _member("600000", date(2026, 9, 8), "pit"),
        _member("600000", date(2026, 9, 8), "current_snapshot"),
    ]
    result = _merge_members_by_stock(members)
    assert len(result) == 1
    assert result[0]["snapshot_type"] == "current_snapshot"


def test_pit_forward_apply_does_not_duplicate_current() -> None:
    """PIT 月末事件前向沿用与当前快照并存时不翻倍。"""
    members = [
        _member("600000", date(2026, 8, 31), "pit"),
        _member("600000", date(2026, 9, 8), "current_snapshot"),
        _member("000001", date(2026, 8, 31), "pit"),
    ]
    result = _merge_members_by_stock(members)
    by_code = {row["stock_code"]: row for row in result}
    assert len(result) == 2
    assert by_code["600000"]["snapshot_type"] == "current_snapshot"
    assert by_code["000001"]["snapshot_type"] == "pit"
