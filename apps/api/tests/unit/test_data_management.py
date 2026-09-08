"""统一数据管理静态目录的基础测试。"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from quant_etf_api.services.data_management_service import (
    DATASETS,
    DataManagementService,
    _dataset_status,
)


def _definition(dataset_key: str):
    """按键返回静态数据集定义。"""
    return next(item for item in DATASETS if item.key == dataset_key)


def _inspect_payload(status: str = "healthy") -> dict:
    """构造最小健康载荷，用于纯逻辑测试。"""
    return {
        "": {
            "partition_name": None,
            "health_status": status,
            "source_name": "akshare",
            "earliest_date": date(2026, 1, 1),
            "latest_date": date(2026, 9, 8),
            "expected_date": date(2026, 9, 8),
            "record_count": 180,
            "missing_count": 0,
            "invalid_count": 0,
            "issue_summary": None,
        }
    }


def _make_service() -> DataManagementService:
    """构造注入 MagicMock 会话的服务实例。"""
    svc = DataManagementService(MagicMock())
    svc._db.query.return_value.filter.return_value.all.return_value = []
    return svc


def _capture_save_calls(svc: DataManagementService) -> list[tuple[str, dict]]:
    """将 _save_snapshot 替换为记录调用参数的空实现。"""
    calls: list[tuple[str, dict]] = []

    def fake_save(definition, partition_key, run_id, operation, payload, existing=None):
        """记录 key 与载荷。"""
        calls.append((partition_key, dict(payload)))
        return MagicMock(partition_key=partition_key)

    svc._save_snapshot = fake_save
    return calls


def test_all_non_news_external_datasets_are_registered() -> None:
    """验证首版非新闻外部数据集均在静态目录中注册。"""
    keys = {item.key for item in DATASETS}
    assert {
        "trading_calendar",
        "index_daily_bar",
        "index_valuation",
        "macro_indicator",
        "index_membership",
        "industry_universe",
        "industry_daily_bar",
        "industry_membership",
        "stock_universe",
        "stock_daily_close",
    } <= keys
    # research_run_item.index_code 已加宽到 64；此断言防止未来键超长再次触发截断
    assert all(len(item.key) <= 64 for item in DATASETS)


def test_all_datasets_support_the_standard_maintenance_operations() -> None:
    """验证所有受管数据集统一声明四类维护操作。"""
    expected = {"sync_latest", "check", "repair_gaps", "rebuild"}
    for item in DATASETS:
        assert expected <= set(item.operations)


def test_warning_quality_rule_does_not_upgrade_to_error() -> None:
    """验证零量等告警不会再被汇总层误标为硬错误。"""
    service = DataManagementService(MagicMock())

    status = service._health_status(
        "index_daily_bar",
        "000300",
        100,
        date(2026, 9, 8),
        date(2026, 9, 8),
        0,
        0,
        2,
    )

    assert status == "warning"


def test_missing_count_reuses_preloaded_calendar_bounds() -> None:
    """验证缺口统计只依赖预加载日历边界，不需要加载逐日行情。"""
    service = DataManagementService(MagicMock())

    missing = service._missing_count_from_bounds(
        date(2026, 9, 1),
        3,
        [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)],
    )

    assert missing == 1


def test_non_partitioned_dataset_check_keeps_single_summary_row() -> None:
    """无分区数据集检查结果应作为汇总行保留，不再被空聚合覆盖为 unknown。"""
    svc = _make_service()
    svc._inspect_many = lambda definition, partitions: _inspect_payload()
    calls = _capture_save_calls(svc)

    result = svc._check(_definition("trading_calendar"), None, "run-1", "check")

    assert [key for key, _ in calls] == [""]
    assert len(result) == 1
    assert calls[0][1]["health_status"] == "healthy"
    assert calls[0][1]["record_count"] == 180


def test_partition_only_check_does_not_refresh_aggregate_row() -> None:
    """单分区检查只应更新该分区，避免汇总行被标记为整集已检查。"""
    svc = _make_service()
    svc._inspect_many = lambda definition, partitions: {
        "000300": _inspect_payload()[""],
    }
    calls = _capture_save_calls(svc)

    svc._check(_definition("index_daily_bar"), "000300", "run-1", "check")

    assert [key for key, _ in calls] == ["000300"]


def test_dataset_status_derivation() -> None:
    """数据集状态应由分区错误与写入记录数共同决定。"""
    assert _dataset_status({"records": 0, "errors": []}) == "success"
    assert _dataset_status({"records": 3, "errors": ["000300: boom"]}) == "partial_success"
    assert _dataset_status({"records": 0, "errors": ["000300: boom"]}) == "failed"


def test_rebuild_replacement_span_guard() -> None:
    """重拉数据明显少于/未覆盖现有数据时应拒绝替换。"""
    svc = DataManagementService(MagicMock())
    existing_min = date(2020, 1, 2)
    existing_max = date(2026, 9, 8)
    fetched = {
        existing_min + timedelta(days=i)
        for i in range(0, (existing_max - existing_min).days + 1, 1)
    }
    svc._ensure_replacement_span(
        "指数 000300", len(fetched), existing_min, existing_max, fetched
    )

    with pytest.raises(RuntimeError, match="明显少于库内现有数据"):
        svc._ensure_replacement_span(
            "指数 000300",
            len(fetched),
            existing_min,
            existing_max,
            set(list(fetched)[: len(fetched) // 2]),
        )
    with pytest.raises(RuntimeError, match="日期范围未覆盖"):
        svc._ensure_replacement_span(
            "指数 000300",
            len(fetched),
            existing_min,
            existing_max,
            fetched - {existing_min},
        )
