"""SystemService 状态快照查询的性能回归测试。

历史问题：``GET /api/system/status`` 为每张数据表执行 ``count(*)`` 与
``max(ingested_at)``，二者在 PostgreSQL 中都需要全表扫描。``stock_daily_close``
约 1250 万行时，接口整体耗时约 15 秒，导致总览页长时间 loading。

本测试锁定两条性能契约：

1. 估算行数达到阈值的表不得生成 ``count(*)``（改为使用统计估算值）；
2. 行数统计读取失败时整体降级，不阻断状态接口。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from quant_etf_api.services.system_service import SystemService

# 被测服务使用的阈值，测试中显式引用避免与实现漂移
THRESHOLD = SystemService._ESTIMATED_COUNT_THRESHOLD


def _make_service(db: MagicMock) -> SystemService:
    """构造仅替换数据库会话的 SystemService（仓库不参与被测路径）。"""
    return SystemService(db, index_repo=MagicMock(), run_repo=MagicMock())


def _patch_snapshot_result(db: MagicMock) -> MagicMock:
    """让链式查询返回固定聚合结果，并回传被查询的计数表达式对象。"""
    chained = db.query.return_value
    chained.add_columns.return_value = chained
    chained.one.return_value = SimpleNamespace(cnt=42, max_date=None, max_ingested=None)
    return chained


class TestEstimatedCountThreshold:
    """大表记录数使用统计估算，不做全表扫描。"""

    def test_large_table_skips_count_star(self) -> None:
        """估算行数超过阈值时，生成的聚合表达式不含 count(*)。"""
        db = MagicMock()
        _patch_snapshot_result(db)

        snapshot = _make_service(db)._get_table_snapshot(
            MagicMock(),
            source_name="个股日线行情",
            table_name="stock_daily_close",
            estimated_rows=THRESHOLD,
        )

        count_expression = str(db.query.call_args.args[0]).lower()
        assert "count" not in count_expression
        assert snapshot.record_count == 42

    def test_small_table_keeps_exact_count(self) -> None:
        """估算行数低于阈值时仍执行精确 count(*)。"""
        db = MagicMock()
        _patch_snapshot_result(db)

        _make_service(db)._get_table_snapshot(
            MagicMock(),
            source_name="宏观经济指标",
            table_name="macro_indicator",
            estimated_rows=THRESHOLD - 1,
        )

        count_expression = str(db.query.call_args.args[0]).lower()
        assert "count" in count_expression

    def test_missing_estimate_falls_back_to_exact_count(self) -> None:
        """统计信息缺失（None）时回退到精确计数，保证首次运行准确。"""
        db = MagicMock()
        _patch_snapshot_result(db)

        _make_service(db)._get_table_snapshot(
            MagicMock(),
            source_name="个股日线行情",
            table_name="stock_daily_close",
            estimated_rows=None,
        )

        count_expression = str(db.query.call_args.args[0]).lower()
        assert "count" in count_expression

    def test_query_failure_returns_zero_snapshot(self) -> None:
        """聚合查询抛错时返回全零快照，不向上冒泡异常。"""
        db = MagicMock()
        db.query.side_effect = RuntimeError("boom")

        snapshot = _make_service(db)._get_table_snapshot(
            MagicMock(),
            source_name="个股日线行情",
            table_name="stock_daily_close",
        )

        assert snapshot.record_count == 0
        assert snapshot.latest_trade_date is None
        db.rollback.assert_called_once()


class TestLoadRowEstimates:
    """pg_class 统计值批量读取。"""

    def test_maps_reltuples_by_table_name(self) -> None:
        """把 pg_class 结果映射为表名到行数的字典。"""
        db = MagicMock()
        db.execute.return_value.all.return_value = [
            SimpleNamespace(relname="stock_daily_close", reltuples=12521302.0),
            SimpleNamespace(relname="macro_indicator", reltuples=2213.0),
        ]

        estimates = _make_service(db)._load_row_estimates(["stock_daily_close", "macro_indicator"])

        assert estimates == {"stock_daily_close": 12521302, "macro_indicator": 2213}

    def test_skips_unanalyzed_tables(self) -> None:
        """reltuples 为 -1（从未 ANALYZE）的表不返回估算值。"""
        db = MagicMock()
        db.execute.return_value.all.return_value = [
            SimpleNamespace(relname="stock_daily_close", reltuples=-1.0),
        ]

        assert _make_service(db)._load_row_estimates(["stock_daily_close"]) == {}

    def test_empty_table_list_skips_query(self) -> None:
        """表名列表为空时不发起查询。"""
        db = MagicMock()

        assert _make_service(db)._load_row_estimates([]) == {}
        db.execute.assert_not_called()

    def test_query_failure_degrades_to_empty(self) -> None:
        """统计查询失败时返回空字典，由调用方回退精确计数。"""
        db = MagicMock()
        db.execute.side_effect = RuntimeError("boom")

        assert _make_service(db)._load_row_estimates(["stock_daily_close"]) == {}
        db.rollback.assert_called_once()


class TestStatusDegradedMode:
    """数据库不可达时直接返回降级状态。"""

    def test_disconnected_db_skips_snapshots(self) -> None:
        """连接检测失败时不再查询任何数据源快照。"""
        db = MagicMock()
        db.execute.side_effect = RuntimeError("connection refused")

        response = _make_service(db).status()

        assert response.db_connected is False
        assert response.data_sources == []
        assert response.recent_runs == []
        assert response.active_index_count == 0
