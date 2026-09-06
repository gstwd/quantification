"""个股日线质量统计纯函数测试。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant_etf_api.domain.stocks.quality import compute_stock_quality


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
    """完整覆盖期望区间时缺失数为 0。"""
    actual = _trading_days()
    result = compute_stock_quality(
        actual_dates=actual,
        trading_days=_trading_days(),
        expected_start=date(2024, 1, 2),
        expected_end=date(2024, 1, 15),
    )
    assert result == {
        "data_start_date": date(2024, 1, 2),
        "data_end_date": date(2024, 1, 15),
        "bar_count": 10,
        "missing_day_count": 0,
    }


def test_quality_counts_suspension_and_gap() -> None:
    """停牌/缺口按交易日历全口径计入缺失。"""
    actual = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 8),
        date(2024, 1, 9),
        date(2024, 1, 10),
    ]
    result = compute_stock_quality(
        actual_dates=actual,
        trading_days=_trading_days(),
        expected_start=date(2024, 1, 2),
        expected_end=date(2024, 1, 15),
    )
    assert result["bar_count"] == 5
    assert result["missing_day_count"] == 5


def test_quality_delist_endpoint() -> None:
    """退市股票只统计到退市日。"""
    trading = _trading_days()
    actual = trading[:7]
    result = compute_stock_quality(
        actual_dates=actual,
        trading_days=trading,
        expected_start=date(2024, 1, 2),
        expected_end=date(2024, 1, 10),
    )
    assert result["missing_day_count"] == 0


def test_quality_no_data_and_no_expected_range() -> None:
    """无实际数据且无期望范围时返回空快照。"""
    result = compute_stock_quality(
        actual_dates=[],
        trading_days=_trading_days(),
        expected_start=None,
        expected_end=None,
    )
    assert result["data_start_date"] is None
    assert result["data_end_date"] is None
    assert result["bar_count"] == 0
    assert result["missing_day_count"] is None


def test_quality_out_of_range_rows_not_counted() -> None:
    """期望区间外的实际行不参与缺失扣减。"""
    result = compute_stock_quality(
        actual_dates=[date(2023, 12, 29), *_trading_days()],
        trading_days=_trading_days(),
        expected_start=date(2024, 1, 2),
        expected_end=date(2024, 1, 15),
    )
    assert result["bar_count"] == 11
    assert result["missing_day_count"] == 0


def test_exchange_frame_normalize() -> None:
    """交易所名单归一化：代码补零、日期转换、活跃标记。"""
    from quant_etf_api.infra.clients.stock_metadata_client import _rows_from_frame

    frame = pd.DataFrame(
        {
            "证券代码": ["1", "2"],
            "证券简称": ["平安银行", "万科A"],
            "上市日期": ["1991-04-03", "1991-01-29"],
        }
    )
    rows = _rows_from_frame(
        frame,
        code_cols=["证券代码"],
        name_cols=["证券简称"],
        ipo_cols=["上市日期"],
        is_active=True,
        source="akshare_sz",
    )
    assert rows[0]["stock_code"] == "000001"
    assert rows[0]["name_cn"] == "平安银行"
    assert rows[0]["ipo_date"] == date(1991, 4, 3)
    assert rows[0]["is_active"] is True
    assert rows[1]["stock_code"] == "000002"
