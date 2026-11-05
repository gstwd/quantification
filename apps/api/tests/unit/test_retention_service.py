"""研究数据瘦身与历史留存服务的编排测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quant_etf_api.domain.research.retention import SlimCandidate
from quant_etf_api.services.retention_service import RetentionService

_T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
_CID = "c0123456789ab"


def _bt(backtest_id: str, strategy_id: str = "base", **kw: object) -> SlimCandidate:
    """构造候选回测。"""
    fields: dict[str, object] = {
        "backtest_id": backtest_id,
        "strategy_id": strategy_id,
        "status": "success",
        "purpose": "research",
        "optimization_id": None,
        "pruned": False,
    }
    fields.update(kw)
    return SlimCandidate(**fields)  # type: ignore[arg-type]


def _service(
    candidates: list[SlimCandidate], *, latest: set[str] | None = None
) -> RetentionService:
    """构造 Mock 依赖的服务。"""
    svc = RetentionService(db=MagicMock(), job_repo=MagicMock())
    svc._repo = MagicMock()
    svc._repo.find_slim_candidates.return_value = candidates
    svc._repo.find_lifecycle_backtest_ids.return_value = set()
    svc._repo.find_sessions.return_value = []
    svc._repo.find_latest_research_backtest_ids.return_value = latest or set()
    svc._repo.prune_index_detail.return_value = 100
    svc._strategy_repo = MagicMock()
    svc._strategy_repo.find_by_id.return_value = SimpleNamespace(
        strategy_id="base", lineage_id="base"
    )
    svc._strategy_repo.find_lineage_members.return_value = [
        SimpleNamespace(strategy_id="base", is_variant=False),
        SimpleNamespace(strategy_id="base__rbab_x", is_variant=True),
    ]
    svc._campaign_repo = MagicMock()
    svc._campaign_repo.find.return_value = SimpleNamespace(
        campaign_id=_CID,
        lineage_id="base",
        status="stopped",
        created_at=_T0,
        finished_at=_T0 + timedelta(hours=3),
    )
    return svc


class TestSlim:
    """逐指数明细瘦身。"""

    def test_dry_run_writes_nothing(self) -> None:
        """预演只统计，不删除也不提交。"""
        svc = _service([_bt("a"), _bt("b"), _bt("latest")], latest={"latest"})
        result = svc.slim_backtest_details(strategy_id="base")
        assert result is not None and result.dry_run
        assert result.eligible_backtests == 2 and result.remaining_backtests == 2
        assert result.protected == {"latest_research": 1}
        assert result.strategy_count == 2
        svc._repo.prune_index_detail.assert_not_called()
        svc._db.commit.assert_not_called()

    def test_latest_protection_excludes_variants(self) -> None:
        """只为非变体成员保护最新研究回测，变体不各留一条。"""
        svc = _service([])
        svc.slim_backtest_details(strategy_id="base")
        assert svc._repo.find_latest_research_backtest_ids.call_args.args[0] == ["base"]

    def test_apply_commits_per_backtest_and_continues_after_failure(self) -> None:
        """每条回测单独提交；单条失败回滚后继续处理其余回测。"""
        svc = _service([_bt("a"), _bt("b"), _bt("c")])
        svc._repo.prune_index_detail.side_effect = [10, RuntimeError("lock"), 30]
        result = svc.slim_backtest_details(strategy_id="base", dry_run=False)
        assert result is not None
        assert result.slimmed_backtests == 2
        assert result.index_rows_deleted == 40
        assert [f.backtest_id for f in result.failed] == ["b"]
        assert svc._db.commit.call_count == 2
        svc._db.rollback.assert_called_once()

    def test_skipped_on_recheck_not_counted(self) -> None:
        """复核不通过（返回 None）的回测不计入已瘦身。"""
        svc = _service([_bt("a")])
        svc._repo.prune_index_detail.return_value = None
        result = svc.slim_backtest_details(strategy_id="base", dry_run=False)
        assert result is not None and result.slimmed_backtests == 0

    def test_max_backtests_limits_batch(self) -> None:
        """max_backtests 限制本批数量，剩余数量回报给调用方。"""
        svc = _service([_bt("a"), _bt("b"), _bt("c")])
        result = svc.slim_backtest_details(strategy_id="base", dry_run=False, max_backtests=2)
        assert result is not None
        assert result.slimmed_backtests == 2 and result.remaining_backtests == 1

    def test_missing_strategy_returns_none(self) -> None:
        """策略不存在返回 None。"""
        svc = _service([])
        svc._strategy_repo.find_by_id.return_value = None
        assert svc.slim_backtest_details(strategy_id="nope") is None

    def test_scope_must_be_exactly_one(self) -> None:
        """策略与战役范围必须且只能指定一个。"""
        svc = _service([])
        with pytest.raises(ValueError):
            svc.slim_backtest_details()
        with pytest.raises(ValueError):
            svc.slim_backtest_details(strategy_id="base", campaign_id=_CID)

    def test_campaign_scope_uses_time_window(self) -> None:
        """战役范围按战役创建到结束的时间窗查询。"""
        svc = _service([_bt("a")])
        result = svc.slim_backtest_details(campaign_id=_CID)
        assert result is not None and result.scope == "campaign"
        kwargs = svc._repo.find_slim_candidates.call_args.kwargs
        assert kwargs["created_from"] == _T0
        assert kwargs["created_to"] == _T0 + timedelta(hours=3)

    def test_running_campaign_rejected(self) -> None:
        """运行中的战役不能瘦身。"""
        svc = _service([])
        svc._campaign_repo.find.return_value.status = "running"
        with pytest.raises(ValueError, match="仍在运行"):
            svc.slim_backtest_details(campaign_id=_CID)


class TestCampaignCleanup:
    """战役结束清理。"""

    def _workspace(self, tmp_path: Path) -> Path:
        """构造战役工作目录：steps/ 下有日志，根目录有报告与候选配置。"""
        workspace = tmp_path / "candidates" / _CID
        (workspace / "steps").mkdir(parents=True)
        (workspace / "steps" / "r01_researcher_abc.json").write_text("{}", encoding="utf-8")
        (workspace / "steps" / "r01_researcher_abc.stderr.txt").write_text("x", encoding="utf-8")
        (workspace / "report.md").write_text("# r", encoding="utf-8")
        (workspace / "r01_candidate.json").write_text("{}", encoding="utf-8")
        return workspace

    def test_cleans_jobs_steps_and_details(self, tmp_path: Path) -> None:
        """删除任务记录、steps/ 与逐指数明细；报告和候选配置保留。"""
        workspace = self._workspace(tmp_path)
        svc = _service([_bt("a"), _bt("b")])
        svc._job_repo.delete_finished_by_keys.return_value = 2
        summary = svc.cleanup_after_campaign(_CID, workspace)
        assert summary["errors"] == []
        assert summary["jobs_deleted"] == 2
        assert svc._job_repo.delete_finished_by_keys.call_args.args[0] == [
            "backtest:a",
            "backtest:b",
        ]
        assert summary["step_logs_deleted"] == 2
        assert not (workspace / "steps").exists()
        assert (workspace / "report.md").exists()
        assert (workspace / "r01_candidate.json").exists()
        assert summary["slim"]["slimmed_backtests"] == 2

    def test_one_failing_part_does_not_stop_others(self, tmp_path: Path) -> None:
        """任务记录清理失败时，步骤日志与瘦身照常执行，错误进入汇总。"""
        workspace = self._workspace(tmp_path)
        svc = _service([_bt("a")])
        svc._job_repo.delete_finished_by_keys.side_effect = RuntimeError("db")
        summary = svc.cleanup_after_campaign(_CID, workspace)
        assert any("任务记录" in e for e in summary["errors"])
        assert not (workspace / "steps").exists()
        assert summary["slim"]["slimmed_backtests"] == 1

    def test_rejects_unexpected_workspace(self, tmp_path: Path) -> None:
        """战役 ID 格式不合法或目录名不匹配时不删除任何文件。"""
        workspace = self._workspace(tmp_path)
        svc = _service([])
        svc._campaign_repo.find.return_value.campaign_id = "c1"
        summary = svc.cleanup_after_campaign("c1", workspace)
        assert any("步骤日志" in e for e in summary["errors"])
        assert (workspace / "steps").exists()

    def test_missing_steps_dir_is_noop(self, tmp_path: Path) -> None:
        """没有 steps/ 目录时计 0，不报错。"""
        workspace = tmp_path / "candidates" / _CID
        workspace.mkdir(parents=True)
        svc = _service([])
        summary = svc.cleanup_after_campaign(_CID, workspace)
        assert summary["step_logs_deleted"] == 0
        assert summary["errors"] == []

    def test_missing_campaign(self, tmp_path: Path) -> None:
        """战役不存在时只返回错误，不抛异常。"""
        svc = _service([])
        svc._campaign_repo.find.return_value = None
        summary = svc.cleanup_after_campaign(_CID, tmp_path)
        assert summary["errors"]


class TestHistoryRetention:
    """任务历史与已读告警留存。"""

    def test_job_history_cutoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """按留存天数计算截止时间。"""
        monkeypatch.setattr("quant_etf_api.services.retention_service.utcnow_aware", lambda: _T0)
        svc = _service([])
        svc._job_repo.delete_finished_before.return_value = 5
        assert svc.prune_job_history(7) == 5
        assert svc._job_repo.delete_finished_before.call_args.args[0] == _T0 - timedelta(days=7)

    def test_read_alerts_commit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """已读告警删除后提交；失败回滚并上抛。"""
        monkeypatch.setattr("quant_etf_api.services.retention_service.utcnow_aware", lambda: _T0)
        svc = _service([])
        svc._repo.delete_read_alerts_before.return_value = 3
        assert svc.prune_read_alerts(30) == 3
        assert svc._repo.delete_read_alerts_before.call_args.args[0] == _T0 - timedelta(days=30)
        svc._db.commit.assert_called_once()
        svc._repo.delete_read_alerts_before.side_effect = RuntimeError("x")
        with pytest.raises(RuntimeError):
            svc.prune_read_alerts(30)
        svc._db.rollback.assert_called_once()
