"""全局数据同步处理器（data_sync_all）的因子门控测试。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from quant_etf_api.infra.job_queue import handlers


def _item(dataset_key: str, status: str = "success", records: int = 0) -> dict:
    """构造执行结果中的数据集条目。"""
    return {
        "dataset_key": dataset_key,
        "status": status,
        "records": records,
        "errors": [],
    }


def _patch_sync_env(monkeypatch, items: list[dict], latest_dates: list[date]):
    """替换数据管理执行与队列依赖。"""
    monkeypatch.setattr(
        handlers,
        "handle_data_management_operation",
        lambda payload: {"items": items, "status": "success"},
    )
    fake_queue = MagicMock()
    monkeypatch.setattr(
        "quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue
    )
    fake_db = MagicMock()
    fake_db.query.return_value.scalar.side_effect = latest_dates
    monkeypatch.setattr("quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db)
    return fake_queue


def test_critical_inputs_ok_enqueues_industry_factor_on_actual_bar_date(
    monkeypatch,
) -> None:
    """行业三项关键输入成功时应按库内实际最新行业交易日入队因子计算。"""
    items = [
        _item("industry_daily_bar", records=20),
        _item("industry_membership"),
        _item("stock_daily_close"),
        _item("index_daily_bar"),
        _item("index_valuation"),
    ]
    fake_queue = _patch_sync_env(monkeypatch, items, [date(2026, 9, 8)])

    handlers.handle_data_sync_all({})

    industry_calls = [
        call
        for call in fake_queue.enqueue.call_args_list
        if call.args[0] == "industry_factor_compute"
    ]
    assert len(industry_calls) == 1
    assert industry_calls[0].args[1] == {"trade_date": "2026-09-08"}


def test_industry_failure_skips_factor_but_index_new_data_still_triggers(
    monkeypatch,
) -> None:
    """行业输入失败时跳过行业因子；指数有新记录时仍入队通用因子计算。"""
    items = [
        _item("industry_daily_bar", status="partial_success", records=3),
        _item("industry_membership"),
        _item("stock_daily_close"),
        _item("index_daily_bar", records=5),
        _item("index_valuation"),
    ]
    fake_queue = _patch_sync_env(monkeypatch, items, [date(2026, 9, 8)])

    handlers.handle_data_sync_all({})

    job_types = [call.args[0] for call in fake_queue.enqueue.call_args_list]
    assert "industry_factor_compute" not in job_types
    factor_calls = [
        call for call in fake_queue.enqueue.call_args_list
        if call.args[0] == "factor_computation"
    ]
    assert len(factor_calls) == 1
    assert factor_calls[0].args[1] == {"trade_date": "2026-09-08"}


def test_no_new_records_does_not_requeue_any_factor(monkeypatch) -> None:
    """各数据集都无新增记录（重复同步）时不应重复入队任何因子计算。"""
    items = [
        _item("industry_daily_bar"),
        _item("industry_membership"),
        _item("stock_daily_close"),
        _item("index_daily_bar"),
        _item("index_valuation"),
    ]
    fake_queue = _patch_sync_env(monkeypatch, items, [date(2026, 9, 8)])

    handlers.handle_data_sync_all({})

    job_types = [call.args[0] for call in fake_queue.enqueue.call_args_list]
    assert "factor_computation" not in job_types
    assert "industry_factor_compute" not in job_types
