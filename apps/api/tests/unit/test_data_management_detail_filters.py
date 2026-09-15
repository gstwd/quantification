"""统一数据管理分区明细接口的筛选能力测试。

覆盖新增的服务端筛选（问题分区 / 健康状态 / 关键字）与路由参数校验：
分区级快照可能上千条，前端必须能直接筛出问题分区，而不是逐页翻找。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from quant_etf_api.main import app
from quant_etf_api.schemas.data_management import DataSetHealthSummary
from quant_etf_api.services.data_management_service import (
    DataManagementService,
    _escape_like_pattern,
)


@pytest.fixture()
def client() -> TestClient:
    """构造测试客户端（不使用 with，避免触发 lifespan 副作用）。"""
    return TestClient(app, raise_server_exceptions=False)


def _make_service() -> tuple[DataManagementService, MagicMock]:
    """构造带桩摘要/分区转换的服务实例，并返回可断言的查询 mock。"""
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.count.return_value = 0
    query.all.return_value = []
    svc = DataManagementService(db)
    svc._summary = lambda definition: DataSetHealthSummary(
        dataset_key=definition.key,
        display_name=definition.name,
        frequency=definition.frequency,
        source_label=definition.source_label,
        supported_operations=list(definition.operations),
        health_status="unknown",
    )
    svc._partition_schema = lambda definition, row: None
    return svc, query


def _compiled_last_filter(query: MagicMock) -> str:
    """编译最后一次 filter 的条件为 SQL 文本，便于断言筛选语义。"""
    criterion = query.filter.call_args_list[-1][0][0]
    return str(
        criterion.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def _last_filter_like_patterns(query: MagicMock) -> list[str]:
    """提取最后一次 filter 中 ILIKE 的绑定参数值，用于断言转义后的模式。"""
    criterion = query.filter.call_args_list[-1][0][0]
    return [clause.right.value for clause in criterion.clauses]


def test_escape_like_pattern_escapes_wildcards() -> None:
    """关键字中的 LIKE 通配符必须转义，避免用户输入被当作模式匹配。"""
    assert _escape_like_pattern("600519") == "600519"
    assert _escape_like_pattern("50%_off") == "50\\%\\_off"
    assert _escape_like_pattern("a\\b") == "a\\\\b"


def test_detail_without_filter_keeps_single_scope_filter() -> None:
    """不传筛选参数时只保留数据集与分区两条件，不额外过滤。"""
    svc, query = _make_service()

    svc.detail("macro_indicator", 0, 50)

    assert query.filter.call_count == 1


def test_detail_problem_only_filters_warning_and_error() -> None:
    """problem_only 应把过滤条件收敛为异常与需关注两类状态。"""
    svc, query = _make_service()

    svc.detail("stock_daily_close", 0, 50, problem_only=True)

    assert query.filter.call_count == 2
    sql = _compiled_last_filter(query)
    assert "health_status IN ('warning', 'error')" in sql


def test_detail_health_status_filter_takes_precedence_over_problem_only() -> None:
    """显式状态优先于 problem_only，且按单值精确过滤。"""
    svc, query = _make_service()

    svc.detail("stock_daily_close", 0, 50, health_status="unknown", problem_only=True)

    assert query.filter.call_count == 2
    assert "health_status = 'unknown'" in _compiled_last_filter(query)


def test_detail_keyword_matches_code_or_name_with_escaped_pattern() -> None:
    """关键字应同时匹配分区代码与名称，并转义输入的 LIKE 通配符。"""
    svc, query = _make_service()

    svc.detail("stock_daily_close", 0, 50, keyword="6005_19")

    assert query.filter.call_count == 2
    sql = _compiled_last_filter(query)
    assert "partition_key ILIKE" in sql
    assert "partition_name ILIKE" in sql
    # 下划线被转义，避免把用户输入当成单字符通配符
    assert _last_filter_like_patterns(query) == ["%6005\\_19%", "%6005\\_19%"]


def test_detail_separates_aggregate_row_from_partitions() -> None:
    """汇总行（partition_key 为空串）不应混入分区明细。"""
    svc, query = _make_service()

    svc.detail("macro_indicator", 0, 50)

    assert query.filter.call_count == 1
    criteria = query.filter.call_args_list[0][0]
    sql = " AND ".join(
        str(
            item.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        for item in criteria
    )
    assert "partition_key != ''" in sql


class TestDetailFilterValidation:
    """路由参数校验。"""

    def test_unknown_health_status_returns_422(self, client: TestClient) -> None:
        """非法健康状态应显式报 422，而不是静默返回空列表。"""
        response = client.get(
            "/api/data-management/datasets/macro_indicator", params={"health_status": "broken"}
        )
        assert response.status_code == 422
        assert "未知健康状态" in response.json()["detail"]
