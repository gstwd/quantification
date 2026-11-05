"""研究数据瘦身仓库生成的 SQL（PostgreSQL 方言编译，不连库）。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from quant_etf_api.infra.db.repositories.retention import RetentionRepository
from quant_etf_api.infra.job_queue.repository import JobRepository

_TS = datetime(2026, 11, 6, tzinfo=timezone.utc)


def _sql(stmt: object) -> str:
    """编译为 PostgreSQL SQL 文本（内联参数）。"""
    return str(
        stmt.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _executed(db: MagicMock) -> list[str]:
    """取出仓库执行过的全部语句。"""
    return [_sql(call.args[0]) for call in db.execute.call_args_list]


class TestRetentionRepositorySql:
    """查询与删除语句。"""

    def test_latest_research_uses_distinct_on(self) -> None:
        """每个策略最新一条成功研究回测用 DISTINCT ON，并跳过已瘦身的回测。"""
        db = MagicMock()
        db.execute.return_value.all.return_value = [("bt1",)]
        assert RetentionRepository(db).find_latest_research_backtest_ids(["s1"]) == {"bt1"}
        sql = _executed(db)[0]
        assert "DISTINCT ON (backtest_run.strategy_id)" in sql
        assert "backtest_run.purpose = 'research'" in sql
        assert "backtest_run.status = 'success'" in sql
        assert "index_detail_pruned_at IS NULL" in sql

    def test_prune_locks_and_rechecks(self) -> None:
        """先 FOR UPDATE 复核，再删除明细并打标记。"""
        db = MagicMock()
        db.execute.return_value.one_or_none.return_value = SimpleNamespace(
            status="success", purpose="research", index_detail_pruned_at=None
        )
        db.execute.return_value.rowcount = 42
        assert RetentionRepository(db).prune_index_detail("bt1", _TS) == 42
        select_sql, delete_sql, update_sql = _executed(db)
        assert "FOR UPDATE" in select_sql
        assert delete_sql.startswith("DELETE FROM backtest_index_result")
        assert "backtest_index_result.backtest_id = 'bt1'" in delete_sql
        assert update_sql.startswith("UPDATE backtest_run SET index_detail_pruned_at")

    def test_prune_skips_when_recheck_fails(self) -> None:
        """复核发现已瘦身 / 非研究用途 / 未结束时不删除。"""
        for row in (
            SimpleNamespace(status="success", purpose="research", index_detail_pruned_at=_TS),
            SimpleNamespace(status="success", purpose="monitor", index_detail_pruned_at=None),
            SimpleNamespace(status="running", purpose="research", index_detail_pruned_at=None),
            None,
        ):
            db = MagicMock()
            db.execute.return_value.one_or_none.return_value = row
            assert RetentionRepository(db).prune_index_detail("bt1", _TS) is None
            assert db.execute.call_count == 1

    def test_read_alerts_only(self) -> None:
        """告警留存只删除已读告警。"""
        db = MagicMock()
        db.execute.return_value.rowcount = 2
        assert RetentionRepository(db).delete_read_alerts_before(_TS) == 2
        sql = _executed(db)[0]
        assert sql.startswith("DELETE FROM system_alert")
        assert "system_alert.read_at IS NOT NULL" in sql


class TestJobHistorySql:
    """任务历史留存只匹配已结束状态。"""

    def _capture(self) -> tuple[JobRepository, MagicMock]:
        """构造带假 Session 的任务仓库。"""
        session = MagicMock()
        session.query.return_value.filter.return_value.delete.return_value = 3
        return JobRepository(session_factory=lambda: session), session

    def test_delete_finished_before(self) -> None:
        """按截止时间删除，且状态过滤只含 success/failed/cancelled。"""
        repo, session = self._capture()
        assert repo.delete_finished_before(_TS) == 3
        criteria = session.query.return_value.filter.call_args.args
        sql = " ".join(_sql(c) for c in criteria)
        assert "'success', 'failed', 'cancelled'" in sql
        for status in ("pending", "running", "paused"):
            assert status not in sql
        session.commit.assert_called_once()

    def test_delete_by_keys_chunks(self) -> None:
        """按去重键删除时分块执行，空列表不访问数据库。"""
        repo, session = self._capture()
        assert repo.delete_finished_by_keys([]) == 0
        session.query.assert_not_called()
        keys = [f"backtest:{i}" for i in range(1200)]
        assert repo.delete_finished_by_keys(keys) == 9
        assert session.query.return_value.filter.call_count == 3
