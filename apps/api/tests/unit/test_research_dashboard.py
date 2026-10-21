"""研究看板（只读 API）单元测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from quant_etf_api.api.routers import research as router
from quant_etf_api.services.research_dashboard_service import ResearchDashboardService
from quant_etf_api.services.strategy_config_service import compute_config_hash

_NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def _service() -> ResearchDashboardService:
    """构造依赖全部 Mock 的看板服务。"""
    svc = ResearchDashboardService(db=MagicMock())
    svc._ledger = MagicMock()
    svc._ledger.trial_breakdown.return_value = {"robustness": 3, "optimization": 1}
    svc._ledger.budget_status.return_value = {"budget": 100, "used": 4, "remaining": 96}
    svc._ledger.list_hypotheses.return_value = []
    svc._campaigns = MagicMock()
    svc._campaign_repo = MagicMock()
    svc._campaign_repo.count_running.return_value = 0
    svc._campaign_repo.latest_review.return_value = None
    svc._strategy_repo = MagicMock()
    svc._opt_repo = MagicMock()
    return svc


def _strategy(strategy_id: str, lineage_id: str | None, **extra: object) -> SimpleNamespace:
    """构造策略行替身。"""
    fields = {
        "strategy_id": strategy_id,
        "lineage_id": lineage_id,
        "display_name": strategy_id,
        "is_variant": False,
        "validation_consumed_at": None,
        "config_json": {"score": {"factors": {"a": 1.0}}},
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


class TestLineages:
    """研究线汇总。"""

    def test_groups_members_and_skips_variant_roots(self) -> None:
        """候选归入基线研究线；根为稳健性变体的分组不展示。"""
        svc = _service()
        svc._strategy_repo.find_all_rows.return_value = [
            _strategy("base", "base"),
            _strategy("base__opt_1", "base", validation_consumed_at=_NOW),
            _strategy("orphan_variant", None, is_variant=True),
        ]
        lineages = svc.list_lineages()
        assert [item.lineage_id for item in lineages] == ["base"]
        assert lineages[0].members == ["base", "base__opt_1"]
        assert lineages[0].n_trials == 4
        assert lineages[0].validation_consumed is True
        assert lineages[0].error is None

    def test_breakdown_failure_is_reported_not_raised(self) -> None:
        """N 统计失败时该研究线带 error 呈现，看板不整体失败。"""
        svc = _service()
        svc._ledger.trial_breakdown.side_effect = RuntimeError("db down")
        svc._strategy_repo.find_all_rows.return_value = [_strategy("base", "base")]
        lineages = svc.list_lineages()
        assert "db down" in (lineages[0].error or "")
        assert lineages[0].n_trials == 0


def _session(**overrides: object) -> SimpleNamespace:
    """构造优化会话替身。"""
    fields = {
        "optimization_id": "o1",
        "strategy_id": "base",
        "candidate_strategy_id": "base__opt_1",
        "candidate_config_hash": "h-candidate",
        "hypothesis": "去掉短期过滤",
        "status": "accepted",
        "execution_model": "t_plus_1_close",
        "created_at": _NOW,
        "finished_at": _NOW,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestCandidates:
    """待人工确认的候选。"""

    def test_promoted_sessions_are_hidden(self) -> None:
        """基线配置已等于候选配置（已 promote）的已接受会话不再列出。"""
        svc = _service()
        baseline = _strategy("base", "base")
        svc._strategy_repo.find_by_id.return_value = baseline
        promoted_hash = compute_config_hash(baseline.config_json)
        svc._opt_repo.find_by_statuses.return_value = [
            _session(optimization_id="o1", candidate_config_hash=promoted_hash),
            _session(optimization_id="o2"),
            _session(optimization_id="o3", status="evaluated"),
        ]
        ids = [item.optimization_id for item in svc.list_candidates()]
        assert ids == ["o2", "o3"]

    def test_landed_as_separate_strategy_is_hidden(self) -> None:
        """候选配置已被另建的策略采用（非 promote）时同样不再列出。"""
        svc = _service()
        baseline = _strategy("base", "base")
        landed = _strategy("base_v2", "base", config_json={"score": {"factors": {"b": 1.0}}})
        svc._strategy_repo.find_by_id.return_value = baseline
        svc._strategy_repo.find_all_rows.return_value = [baseline, landed]
        landed_hash = compute_config_hash(landed.config_json)
        svc._opt_repo.find_by_statuses.return_value = [
            _session(optimization_id="o1", candidate_config_hash=landed_hash)
        ]
        assert svc.list_candidates() == []

    def test_review_verdict_attached(self) -> None:
        """候选带上战役评审结论。"""
        svc = _service()
        svc._strategy_repo.find_by_id.return_value = _strategy("base", "base")
        svc._opt_repo.find_by_statuses.return_value = [_session(status="evaluated")]
        svc._campaign_repo.latest_review.return_value = SimpleNamespace(
            outcome="recommend_accept", campaign_id="c1", summary="10 项全过"
        )
        item = svc.list_candidates()[0]
        assert (item.review_verdict, item.campaign_id) == ("recommend_accept", "c1")

    def test_detail_is_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """详情走只读的 inspect，不调用会补写汇总的 show。"""
        svc = _service()
        svc._strategy_repo.find_by_id.return_value = _strategy("base", "base")
        svc._opt_repo.find_by_id.return_value = _session(status="evaluated")
        fake = MagicMock()
        fake.inspect.return_value = {
            "acceptance_checklist": [{"key": "deflated_sharpe", "pass": False}],
            "missing_backtest_ids": [],
        }
        monkeypatch.setattr(
            "quant_etf_api.services.research_dashboard_service.OptimizationService",
            lambda db: fake,
        )
        detail = svc.candidate("o1")
        assert detail is not None
        assert detail.acceptance_checklist[0]["key"] == "deflated_sharpe"
        fake.show.assert_not_called()


class TestRouter:
    """路由错误映射。"""

    def test_missing_resources_return_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不存在的假设 / 战役 / 候选返回 404。"""
        fake = MagicMock()
        fake.hypothesis.return_value = None
        fake.campaign.return_value = None
        fake.candidate.return_value = None
        monkeypatch.setattr(router, "ResearchDashboardService", lambda db: fake)
        for call in (
            lambda: router.get_hypothesis("h", db=MagicMock()),
            lambda: router.get_campaign("c", db=MagicMock()),
            lambda: router.get_candidate("o", db=MagicMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                call()
            assert exc_info.value.status_code == 404

    def test_bad_status_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非法状态过滤返回 422。"""
        fake = MagicMock()
        fake.ledger.side_effect = ValueError("未知的假设状态 'x'")
        monkeypatch.setattr(router, "ResearchDashboardService", lambda db: fake)
        with pytest.raises(HTTPException) as exc_info:
            router.list_hypotheses(
                strategy_id=None, index_code=None, tag=None, status="x", db=MagicMock()
            )
        assert exc_info.value.status_code == 422
