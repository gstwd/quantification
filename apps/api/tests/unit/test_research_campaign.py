"""研究战役（自动研究循环）单元测试：规则、服务编排、命令拦截与驱动器。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import quant_etf_api.services.campaign_driver as driver
from quant_etf_api.domain.research import campaign as rules
from quant_etf_api.infra.db.models.core import ResearchCampaignModel, ResearchCampaignStepModel
from quant_etf_api.services.research_campaign_service import ResearchCampaignService

_NOW = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)


def _view(round_no: int, role: str, status: str = "done", **extra: Any) -> rules.StepView:
    """构造步骤视图。"""
    return rules.StepView(round=round_no, role=role, status=status, **extra)


class TestPlanNextStep:
    """下一步安排。"""

    def test_first_step_is_researcher(self) -> None:
        """没有步骤时从第 1 轮研究员开始。"""
        assert rules.plan_next_step([]) == rules.NextStep(role="researcher", round=1)

    def test_review_requested_goes_to_reviewer(self) -> None:
        """研究员请求评审 → 同轮评审员，带上评审对象。"""
        nxt = rules.plan_next_step(
            [
                _view(
                    1,
                    "researcher",
                    outcome="review_requested",
                    optimization_id="o1",
                    hypothesis_id="h1",
                )
            ]
        )
        assert nxt == rules.NextStep(
            role="reviewer", round=1, optimization_id="o1", hypothesis_id="h1"
        )

    def test_needs_more_continues_same_hypothesis(self) -> None:
        """评审要求补证据 → 下一轮研究员继续同一假设。"""
        nxt = rules.plan_next_step(
            [
                _view(1, "researcher", outcome="review_requested", optimization_id="o1"),
                _view(
                    1, "reviewer", outcome="needs_more", optimization_id="o1", hypothesis_id="h1"
                ),
            ]
        )
        assert nxt is not None
        assert (nxt.role, nxt.round, nxt.hypothesis_id) == ("researcher", 2, "h1")

    def test_failed_step_retries_in_place(self) -> None:
        """失败步骤原地重试。"""
        nxt = rules.plan_next_step([_view(3, "reviewer", status="failed", optimization_id="o9")])
        assert nxt is not None
        assert (nxt.role, nxt.round, nxt.retry, nxt.optimization_id) == ("reviewer", 3, True, "o9")

    def test_running_step_blocks(self) -> None:
        """有运行中步骤时不安排新步骤。"""
        assert rules.plan_next_step([_view(1, "researcher", status="running")]) is None

    def test_rejected_round_starts_next_round(self) -> None:
        """本轮否决 → 下一轮新研究员。"""
        nxt = rules.plan_next_step([_view(2, "researcher", outcome="rejected")])
        assert nxt == rules.NextStep(role="researcher", round=3)

    @pytest.mark.parametrize("verdict", ["recommend_accept", "reject"])
    def test_reviewer_verdict_starts_fresh_round(self, verdict: str) -> None:
        """评审员给出接受推荐或否决后，下一轮研究员从新方向开始（不带评审对象）。"""
        nxt = rules.plan_next_step(
            [
                _view(1, "researcher", outcome="review_requested", optimization_id="o1"),
                _view(1, "reviewer", outcome=verdict, optimization_id="o1", hypothesis_id="h1"),
            ]
        )
        assert nxt == rules.NextStep(role="researcher", round=2)


class TestStopRules:
    """停止条件。"""

    _BASE: dict[str, Any] = {
        "trials_used": 5,
        "max_trials": 30,
        "lineage_remaining": 50,
        "elapsed_hours": 1.0,
        "max_hours": 6.0,
        "rounds_done": 1,
        "max_rounds": 5,
        "consecutive_rejects": 0,
        "max_consecutive_rejects": 3,
        "consecutive_failures": 0,
        "next_is_new_round": True,
    }

    def test_within_limits(self) -> None:
        """限额内不停。"""
        assert rules.evaluate_stop(**self._BASE) is None

    @pytest.mark.parametrize(
        ("override", "fragment"),
        [
            ({"consecutive_failures": 2}, "失败"),
            ({"elapsed_hours": 6.0}, "时长"),
            ({"trials_used": 30}, "新增试验"),
            ({"lineage_remaining": 0}, "预算"),
            ({"rounds_done": 5}, "轮数"),
            ({"consecutive_rejects": 3}, "未被接受"),
        ],
    )
    def test_each_limit_stops(self, override: dict[str, Any], fragment: str) -> None:
        """任一限额触顶即停。"""
        reason = rules.evaluate_stop(**{**self._BASE, **override})
        assert reason is not None and fragment in reason

    def test_pending_review_is_finished_before_round_limit(self) -> None:
        """轮数触顶但下一步是评审员时，先让评审做完。"""
        assert (
            rules.evaluate_stop(**{**self._BASE, "rounds_done": 5, "next_is_new_round": False})
            is None
        )

    def test_consecutive_counters(self) -> None:
        """连续否决与连续失败只数末尾。"""
        assert (
            rules.count_consecutive_rejects(
                ["rejected", "accepted", "rejected", "blocked_by_schema"]
            )
            == 2
        )
        assert (
            rules.count_consecutive_failures(
                [
                    _view(1, "researcher", status="failed"),
                    _view(1, "researcher"),
                    _view(2, "researcher", status="failed"),
                ]
            )
            == 1
        )


class TestCommandAllowlist:
    """战役模式命令白名单（纯规则）。"""

    @pytest.mark.parametrize(
        ("command", "sub", "flags"),
        [
            ("lifecycle", "online", {}),
            ("stock", "fill", {}),
            ("init-factors", None, {}),
            ("strategy", "consume-validation", {}),
            ("optimization", "finish", {"promote": True, "strict": True}),
            ("optimization", "finish", {"strict": False}),
            ("research", "batch", {"over_budget": "x"}),
            ("research", "campaign", {"campaign_action": "run"}),
            ("research", "campaign", {"campaign_action": "start"}),
            ("backtest", "run", {"purpose": "validation"}),
            ("strategy", "update", {"status": "active"}),
            ("strategy", "update", {"lineage": "other"}),
            ("strategy", "create", {}),
            ("strategy", "create", {"draft": False, "lineage": "base"}),
            ("research", "dsr-calibrate", {}),
            ("strategy", "prune-variants", {}),
            ("backtest", "slim", {}),
            ("backtest", "delete", {}),
        ],
    )
    def test_denied(self, command: str, sub: str | None, flags: dict[str, Any]) -> None:
        """越权命令被拒绝并给出原因。"""
        assert rules.check_command_allowed(command, sub, flags) is not None

    @pytest.mark.parametrize(
        ("command", "sub", "flags"),
        [
            ("research", "batch", {}),
            ("research", "buckets", {}),
            ("research", "campaign", {"campaign_action": "step-done"}),
            ("optimization", "finish", {"strict": True, "verdict": "accept"}),
            ("backtest", "run", {"purpose": "research"}),
            ("backtest", "verify", {}),
            ("strategy", "create", {"draft": True}),
            ("research", "factor-ic", {}),
        ],
    )
    def test_allowed(self, command: str, sub: str | None, flags: dict[str, Any]) -> None:
        """研究所需命令放行。"""
        assert rules.check_command_allowed(command, sub, flags) is None

    def test_file_output_target(self) -> None:
        """只认会按 --file 写文件的命令（含 research campaign report）。"""
        assert (
            rules.file_output_target(
                "research", "campaign", {"campaign_action": "report", "file": "x.md"}
            )
            == "x.md"
        )
        assert rules.file_output_target("optimization", "report", {"file": "y.md"}) == "y.md"
        # strategy create 的 --file 是输入文件，不受工作目录限制
        assert rules.file_output_target("strategy", "create", {"file": "z.json"}) is None

    def test_diagnostic_quota(self) -> None:
        """诊断命令识别与配额判定。"""
        assert rules.is_diagnostic_command("research", "buckets")
        assert rules.is_diagnostic_command("research", "signal-lab")
        assert not rules.is_diagnostic_command("research", "batch")
        assert rules.check_diagnostic_quota(29, 30) is None
        assert "30/30" in (rules.check_diagnostic_quota(30, 30) or "")

    def test_outcome_must_match_role(self) -> None:
        """回报结果须与角色匹配。"""
        with pytest.raises(rules.CampaignRuleError):
            rules.validate_outcome("researcher", "recommend_accept")
        assert rules.validate_outcome("reviewer", "needs_more") == "needs_more"


def _campaign(**overrides: Any) -> ResearchCampaignModel:
    """构造战役行。"""
    fields: dict[str, Any] = {
        "campaign_id": "c1",
        "objective": "000852 择时",
        "strategy_id": "base",
        "lineage_id": "base",
        "status": "running",
        "max_trials": 30,
        "max_hours": 6.0,
        "max_rounds": 5,
        "max_consecutive_rejects": 3,
        "trials_at_start": 10,
        "created_at": _NOW,
    }
    fields.update(overrides)
    return ResearchCampaignModel(**fields)


def _step(**overrides: Any) -> ResearchCampaignStepModel:
    """构造步骤行。"""
    fields: dict[str, Any] = {
        "step_id": "s1",
        "campaign_id": "c1",
        "round": 1,
        "role": "researcher",
        "status": "running",
        "started_at": _NOW,
    }
    fields.update(overrides)
    return ResearchCampaignStepModel(**fields)


def _service(
    campaign: ResearchCampaignModel | None = None,
    steps: list[ResearchCampaignStepModel] | None = None,
    used: int = 12,
) -> ResearchCampaignService:
    """构造 Mock 依赖的战役服务。"""
    svc = ResearchCampaignService(db=MagicMock())
    svc._repo = MagicMock()
    svc._repo.find.return_value = campaign
    steps = steps or []
    svc._repo.list_steps.return_value = steps
    running = [s for s in steps if s.status == "running"]
    svc._repo.find_running_step.return_value = running[-1] if running else None
    svc._repo.list_hypotheses.return_value = []
    svc._repo.other_active_campaigns.return_value = []
    svc._repo.has_review.return_value = False
    svc._ledger = MagicMock()
    svc._ledger.budget_status.return_value = {
        "lineage_id": "base",
        "budget": 100,
        "used": used,
        "remaining": 100 - used,
    }
    svc._ledger.ledger.return_value = {"recent_closed": []}
    svc._config_svc = MagicMock()
    svc._config_svc.lineage_members.return_value = ["base", "base__opt_a"]
    svc._config_svc.lineage_of.side_effect = lambda sid: "base" if sid.startswith("base") else sid
    svc._config_svc.is_composite.return_value = False
    svc._opt_repo = MagicMock()
    svc._cleanup = MagicMock()
    svc._cleanup.cleanup_after_campaign.return_value = {"jobs_deleted": 1, "errors": []}
    return svc


class TestCampaignService:
    """战役服务编排。"""

    def test_start_records_trials_at_start(self) -> None:
        """开启时记录研究线已用 N，后续按增量计。"""
        svc = _service(used=17)
        result = svc.start(
            objective="x",
            strategy_id="base",
            max_trials=20,
            max_hours=4,
            max_rounds=3,
            max_consecutive_rejects=2,
        )
        assert result["trials_at_start"] == 17
        assert result["campaign_id"].startswith("c")
        assert result["status"] == "running"

    def test_start_rejects_missing_strategy(self) -> None:
        """种子策略必须存在。"""
        svc = _service()
        svc._config_svc.get_config.return_value = None
        with pytest.raises(ValueError, match="种子策略"):
            svc.start(
                objective="x",
                strategy_id="nope",
                max_trials=1,
                max_hours=1,
                max_rounds=1,
                max_consecutive_rejects=1,
            )

    def test_next_task_returns_researcher_card(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """限额内给出研究员任务卡（含工作目录与限额）。"""
        monkeypatch.setattr(
            "quant_etf_api.services.research_campaign_service.utcnow_aware",
            lambda: _NOW + timedelta(hours=1),
        )
        svc = _service(_campaign())
        task = svc.next_task("c1")
        assert task["action"] == "run"
        card = task["card"]
        assert card["next"]["role"] == "researcher"
        assert card["workspace"] == "candidates/c1/"
        assert card["limits"]["trials_used"] == 2

    def test_next_task_stops_when_trials_exhausted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """试验增量触顶 → 停止战役。"""
        monkeypatch.setattr(
            "quant_etf_api.services.research_campaign_service.utcnow_aware",
            lambda: _NOW + timedelta(hours=1),
        )
        campaign = _campaign()
        svc = _service(campaign, used=40)
        task = svc.next_task("c1")
        assert task["action"] == "stop"
        assert campaign.status == "stopped"
        assert "新增试验" in campaign.stop_reason

    def test_next_task_stop_includes_cleanup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """停止任务卡带回战役结束清理结果。"""
        monkeypatch.setattr(
            "quant_etf_api.services.research_campaign_service.utcnow_aware",
            lambda: _NOW + timedelta(hours=1),
        )
        svc = _service(_campaign(), used=40)
        task = svc.next_task("c1")
        assert task["action"] == "stop"
        assert task["cleanup"] == {"jobs_deleted": 1, "errors": []}

    def test_stop_cleans_up_once(self) -> None:
        """只有真正把战役从运行中停止的那次调用触发清理。"""
        campaign = _campaign()
        svc = _service(campaign)
        first = svc.stop("c1", "人工停止")
        assert first["cleanup"]["jobs_deleted"] == 1
        second = svc.stop("c1", "再次停止")
        assert "cleanup" not in second
        svc._cleanup.cleanup_after_campaign.assert_called_once()
        args = svc._cleanup.cleanup_after_campaign.call_args.args
        assert args[0] == "c1" and args[1].parts[-2:] == ("candidates", "c1")

    def test_stop_skips_cleanup_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """关闭 campaign_auto_cleanup_enabled 时停止不清理。"""
        monkeypatch.setattr(
            "quant_etf_api.services.research_campaign_service.get_settings",
            lambda: SimpleNamespace(campaign_auto_cleanup_enabled=False),
        )
        svc = _service(_campaign())
        result = svc.stop("c1", "人工停止")
        assert "cleanup" not in result
        svc._cleanup.cleanup_after_campaign.assert_not_called()

    def test_stop_survives_cleanup_failure(self) -> None:
        """清理抛异常时战役仍然停止，错误进入返回值。"""
        campaign = _campaign()
        svc = _service(campaign)
        svc._cleanup.cleanup_after_campaign.side_effect = RuntimeError("boom")
        result = svc.stop("c1", "人工停止")
        assert campaign.status == "stopped"
        assert result["cleanup"] == {"errors": ["boom"]}

    def test_sweep_finished_only_for_ended_campaigns(self) -> None:
        """驱动器收尾补清理：运行中的战役不清理，已结束的可重复清理。"""
        running = _campaign()
        svc = _service(running)
        assert svc.sweep_finished("c1") is None
        running.status = "stopped"
        assert svc.sweep_finished("c1") == {"jobs_deleted": 1, "errors": []}
        assert svc.sweep_finished("c1") == {"jobs_deleted": 1, "errors": []}

    def test_begin_step_refuses_parallel_campaigns(self) -> None:
        """同一时间只允许一个战役在跑。"""
        svc = _service(_campaign())
        svc._repo.other_active_campaigns.return_value = ["c9"]
        with pytest.raises(ValueError, match="同一时间只允许一个战役"):
            svc.begin_step("c1", role="researcher", round_no=1)

    def test_review_requested_needs_evaluated_session(self) -> None:
        """请求评审必须给出研究线内已评估的优化会话。"""
        step = _step()
        svc = _service(_campaign(), [step])
        svc._opt_repo.find_by_id.return_value = SimpleNamespace(
            strategy_id="base", status="running"
        )
        with pytest.raises(ValueError, match="尚未评估完成"):
            svc.step_done("c1", outcome="review_requested", summary="s", optimization_id="o1")
        svc._opt_repo.find_by_id.return_value = SimpleNamespace(
            strategy_id="base", status="evaluated"
        )
        result = svc.step_done("c1", outcome="review_requested", summary="s", optimization_id="o1")
        assert result["status"] == "done"
        assert result["outcome"] == "review_requested"

    def test_no_idea_completes_campaign(self) -> None:
        """研究员判定没有新方向 → 战役完成。"""
        campaign = _campaign()
        svc = _service(campaign, [_step()])
        svc.step_done("c1", outcome="no_idea", summary="范围内的方向已检验完")
        assert campaign.status == "completed"

    def test_reviewer_must_review_before_done(self) -> None:
        """评审员先 review 再 step-done。"""
        step = _step(role="reviewer")
        svc = _service(_campaign(), [step])
        with pytest.raises(ValueError, match="review"):
            svc.step_done("c1", outcome=None, summary="s")
        svc._opt_repo.find_by_id.return_value = SimpleNamespace(
            strategy_id="base", status="evaluated"
        )
        svc.review("c1", optimization_id="o1", verdict="reject", reason="第 8 项不过")
        assert step.outcome == "reject"
        assert svc.step_done("c1", outcome=None, summary="否决")["status"] == "done"

    @pytest.mark.parametrize(
        ("role", "outcome", "exit_code", "expected"),
        [
            ("researcher", None, 0, "failed"),
            ("reviewer", "recommend_accept", 0, "done"),
            ("reviewer", "recommend_accept", 1, "failed"),
        ],
    )
    def test_finalize_step(
        self, role: str, outcome: str | None, exit_code: int, expected: str
    ) -> None:
        """agent 未回报的步骤判失败；评审员已记结论但漏了 step-done 视为完成。"""
        step = _step(role=role, outcome=outcome)
        svc = _service(_campaign(), [step])
        svc._repo.find_step.return_value = step
        result = svc.finalize_step("s1", exit_code=exit_code, cost_usd=1.25)
        assert result["status"] == expected
        assert result["cost_usd"] == 1.25

    def test_recommend_accept_full_chain(self) -> None:
        """评审员推荐接受的全链：review → finish accept 放行 → step-done → 收口为完成。"""
        step = _step(role="reviewer", optimization_id="o1", hypothesis_id="h1")
        svc = _service(_campaign(), [step])
        svc._repo.find_step.return_value = step
        svc._opt_repo.find_by_id.return_value = SimpleNamespace(
            strategy_id="base__opt_a", status="evaluated"
        )
        # 仓库语义：只认 running / done 步骤上的结论
        svc._repo.has_review.side_effect = lambda cid, oid, verdict: (
            step.optimization_id == oid
            and step.outcome == verdict
            and step.status in ("running", "done")
        )
        finish = {"subcommand": "finish", "optimization_id": "o1", "verdict": "accept"}
        with pytest.raises(ValueError, match="recommend_accept"):
            svc.guard_command("c1", "optimization", {**finish, "strict": True})
        svc.review("c1", optimization_id="o1", verdict="recommend_accept", reason="10 项全过")
        assert step.outcome == "recommend_accept"
        svc.guard_command("c1", "optimization", {**finish, "strict": True})
        done = svc.step_done("c1", outcome=None, summary="推荐接受，已严格 finish")
        assert done["status"] == "done"
        assert done["outcome"] == "recommend_accept"
        assert svc.finalize_step("s1", exit_code=0)["status"] == "done"

    def test_failed_review_does_not_unlock_accept(self) -> None:
        """失败评审步骤留下的 recommend_accept 不能在重试步骤里解锁 finish accept。"""
        failed = _step(
            role="reviewer", status="failed", outcome="recommend_accept", optimization_id="o1"
        )
        retry = _step(step_id="s2", role="reviewer", optimization_id="o1")
        steps = [failed, retry]
        svc = _service(_campaign(), steps)
        svc._opt_repo.find_by_id.return_value = SimpleNamespace(strategy_id="base")
        svc._repo.has_review.side_effect = lambda cid, oid, verdict: any(
            s.optimization_id == oid and s.outcome == verdict and s.status in ("running", "done")
            for s in steps
        )
        flags = {
            "subcommand": "finish",
            "optimization_id": "o1",
            "verdict": "accept",
            "strict": True,
        }
        with pytest.raises(ValueError, match="recommend_accept"):
            svc.guard_command("c1", "optimization", flags)

    def test_review_overwrites_previous_verdict(self) -> None:
        """同一评审步骤重复 review 时以最后一次为准。"""
        step = _step(role="reviewer")
        svc = _service(_campaign(), [step])
        svc._opt_repo.find_by_id.return_value = SimpleNamespace(
            strategy_id="base", status="evaluated"
        )
        svc.review("c1", optimization_id="o1", verdict="needs_more", reason="缺邻域证据")
        svc.review("c1", optimization_id="o1", verdict="reject", reason="补证后仍不过")
        assert (step.outcome, step.summary) == ("reject", "补证后仍不过")

    def test_review_rejected_outside_reviewer_or_lineage(self) -> None:
        """研究员步骤不能记评审；研究线外的会话不能被评审。"""
        researcher = _step()
        svc = _service(_campaign(), [researcher])
        svc._opt_repo.find_by_id.return_value = SimpleNamespace(
            strategy_id="base", status="evaluated"
        )
        with pytest.raises(ValueError, match="只有评审员"):
            svc.review("c1", optimization_id="o1", verdict="reject", reason="r")
        reviewer = _step(role="reviewer")
        svc = _service(_campaign(), [reviewer])
        svc._opt_repo.find_by_id.return_value = SimpleNamespace(
            strategy_id="other", status="evaluated"
        )
        with pytest.raises(ValueError, match="不在研究线"):
            svc.review("c1", optimization_id="o1", verdict="reject", reason="r")
        assert reviewer.outcome is None

    def test_guard_scopes_strategy_to_lineage(self) -> None:
        """战役只能检验研究线内的策略。"""
        svc = _service(_campaign())
        with pytest.raises(ValueError, match="研究线"):
            svc.guard_command("c1", "research", {"subcommand": "batch", "strategy_id": "other"})
        svc.guard_command("c1", "research", {"subcommand": "batch", "strategy_id": "base__opt_a"})

    def test_guard_requires_review_before_accept(self) -> None:
        """接受优化会话前必须有 recommend_accept 评审。"""
        svc = _service(_campaign())
        svc._opt_repo.find_by_id.return_value = SimpleNamespace(strategy_id="base")
        flags = {
            "subcommand": "finish",
            "optimization_id": "o1",
            "verdict": "accept",
            "strict": True,
        }
        with pytest.raises(ValueError, match="recommend_accept"):
            svc.guard_command("c1", "optimization", flags)
        svc._repo.has_review.return_value = True
        svc.guard_command("c1", "optimization", flags)

    def test_guard_blocks_editing_baseline(self) -> None:
        """基线与启用策略不能在战役内直接修改。"""
        svc = _service(_campaign())
        svc._config_svc.get_config.return_value = SimpleNamespace(status="active")
        with pytest.raises(ValueError, match="草稿"):
            svc.guard_command("c1", "strategy", {"subcommand": "update", "strategy_id": "base"})

    def test_guard_keeps_report_files_in_workspace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """报告只能写到战役工作目录。"""
        monkeypatch.chdir(tmp_path)
        svc = _service(_campaign())
        svc._opt_repo.find_by_id.return_value = None
        with pytest.raises(ValueError, match="candidates/c1"):
            svc.guard_command("c1", "optimization", {"subcommand": "report", "file": "../x.md"})
        svc.guard_command(
            "c1", "optimization", {"subcommand": "report", "file": "candidates/c1/r01.md"}
        )

    def test_guard_keeps_campaign_report_in_workspace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """research campaign report --file 同样只能写到战役工作目录。"""
        monkeypatch.chdir(tmp_path)
        svc = _service(_campaign())
        flags = {"subcommand": "campaign", "campaign_action": "report", "campaign_id": "c1"}
        with pytest.raises(ValueError, match="candidates/c1"):
            svc.guard_command("c1", "research", {**flags, "file": "AGENTS.md"})
        svc.guard_command("c1", "research", {**flags, "file": "candidates/c1/report.md"})

    def test_guard_rejects_other_campaign_id(self) -> None:
        """agent 不能操作其他战役。"""
        svc = _service(_campaign())
        with pytest.raises(ValueError, match="本战役"):
            svc.guard_command(
                "c1",
                "research",
                {"subcommand": "campaign", "campaign_action": "status", "campaign_id": "c9"},
            )

    def test_guard_create_requires_lineage(self) -> None:
        """战役内新建草稿必须归入战役研究线。"""
        svc = _service(_campaign())
        flags = {"subcommand": "create", "strategy_id": "base__x", "draft": True}
        with pytest.raises(ValueError, match="--lineage"):
            svc.guard_command("c1", "strategy", flags)
        with pytest.raises(ValueError, match="--lineage"):
            svc.guard_command("c1", "strategy", {**flags, "lineage": "other"})
        svc.guard_command("c1", "strategy", {**flags, "lineage": "base"})

    def test_guard_scopes_ids_to_lineage(self) -> None:
        """按 ID 操作的批次、回测与假设限定在本战役 / 研究线内。"""
        svc = _service(_campaign())
        svc._repo.robustness_strategy.return_value = "other"
        with pytest.raises(ValueError, match="稳健性批次"):
            svc.guard_command("c1", "robustness", {"subcommand": "abandon", "robustness_id": "r1"})
        svc._repo.robustness_strategy.return_value = "base__opt_a"
        svc.guard_command("c1", "robustness", {"subcommand": "abandon", "robustness_id": "r1"})

        svc._repo.backtest_strategy.return_value = "other"
        with pytest.raises(ValueError, match="回测"):
            svc.guard_command("c1", "backtest", {"subcommand": "cancel", "backtest_id": "b1"})

        hyp = {"subcommand": "hypothesis", "hypothesis_action": "close", "hypothesis_id": "h1"}
        svc._repo.hypothesis_campaign.return_value = (True, None)
        with pytest.raises(ValueError, match="本战役登记的假设"):
            svc.guard_command("c1", "research", hyp)
        svc._repo.hypothesis_campaign.return_value = (True, "c1")
        svc.guard_command("c1", "research", hyp)

        with pytest.raises(ValueError, match="研究线"):
            svc.guard_command(
                "c1",
                "research",
                {"subcommand": "hypothesis", "hypothesis_action": "add", "strategy_id": "other"},
            )

    def test_guard_counts_diagnostics(self) -> None:
        """诊断命令放行即计数，达到上限后拒绝。"""
        campaign = _campaign(max_diagnostics=2, diagnostics_used=1)
        svc = _service(campaign)
        svc.guard_command("c1", "research", {"subcommand": "factor-ic"})
        svc._repo.increment_diagnostics.assert_called_once_with("c1")
        campaign.diagnostics_used = 2
        with pytest.raises(ValueError, match="2/2"):
            svc.guard_command("c1", "research", {"subcommand": "buckets"})
        # 非诊断命令不计数
        svc.guard_command("c1", "research", {"subcommand": "ledger"})
        assert svc._repo.increment_diagnostics.call_count == 1

    def test_guard_rejects_finished_campaign(self) -> None:
        """战役结束后不再放行任何命令。"""
        svc = _service(_campaign(status="stopped"))
        with pytest.raises(ValueError, match="已结束"):
            svc.guard_command("c1", "research", {"subcommand": "ledger"})


class TestDriver:
    """驱动器：权限、提示、环境与主循环。"""

    def test_permissions_restrict_writes_and_commands(self) -> None:
        """只放行平台 CLI、白名单读取与战役工作目录写入，git 拒绝。"""
        settings = driver.permission_settings("c1")["permissions"]
        assert "Bash(python -m quant_etf_api.cli:*)" in settings["allow"]
        assert "Write(./candidates/c1/**)" in settings["allow"]
        assert "Read(./candidates/c1/**)" in settings["allow"]
        assert "Read(./.claude/skills/**)" in settings["allow"]
        assert "Bash(git:*)" in settings["deny"]
        assert not any(rule.startswith("Bash(") and "cli" not in rule for rule in settings["allow"])
        # 不放行不限路径的读取，也不放行其他战役 / 旧研究目录
        assert not {"Read", "Glob", "Grep"} & set(settings["allow"])
        assert "Read(./candidates/**)" not in settings["allow"]

    def test_run_agent_returns_output(self) -> None:
        """默认 runner 透传标准输入并返回 UTF-8 输出。"""
        proc = driver.run_agent(
            [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
            input="ok",
            timeout=60,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == "OK"

    def test_run_agent_kills_on_timeout(self) -> None:
        """超时时结束进程并抛 TimeoutExpired。"""
        with pytest.raises(subprocess.TimeoutExpired):
            driver.run_agent([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)

    def test_interrupt_marks_step_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """人工中断时把运行中步骤收口为失败再抛出。"""
        monkeypatch.delenv("QUANT_CAMPAIGN_ID", raising=False)
        monkeypatch.setattr(driver, "REPO_ROOT", tmp_path)
        fake = MagicMock()
        fake.next_task.return_value = {
            "action": "run",
            "card": {
                "campaign_id": "c1",
                "workspace": "candidates/c1/",
                "next": {"role": "researcher", "round": 1},
            },
        }
        fake.require.return_value = SimpleNamespace(model=None, max_step_usd=None)
        fake.begin_step.return_value = {"step_id": "abcdef1234"}
        monkeypatch.setattr(driver, "ResearchCampaignService", lambda db: fake)

        def _runner(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            if cmd[1:] == ["auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout='{"loggedIn": true}', stderr="")
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            driver.run_campaign(
                "c1",
                driver.DriverConfig(),
                runner=_runner,
                session_factory=MagicMock,
                echo=lambda _msg: None,
            )
        assert "中断" in fake.finalize_step.call_args.kwargs["error"]

    def test_prompt_names_role_skill_and_card(self) -> None:
        """提示指向角色手册并带完整任务卡。"""
        card = {
            "campaign_id": "c1",
            "workspace": "candidates/c1/",
            "next": {"role": "reviewer", "round": 2, "optimization_id": "o1", "retry": False},
        }
        prompt = driver.build_prompt(card)
        assert "references/reviewer.md" in prompt
        assert "--optimization-id o1" in prompt
        assert json.dumps(card["next"], ensure_ascii=False, indent=2).splitlines()[1] in prompt

    def test_env_marks_campaign_and_strips_nested_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """agent 环境带战役标记与并行数，并清掉嵌套会话标记。"""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH", "1")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://host-proxy.invalid")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-kept")
        env = driver.build_env("c1", 3)
        assert env["QUANT_CAMPAIGN_ID"] == "c1"
        assert env["QUANT_ETF_CLI_WORKERS"] == "3"
        assert "CLAUDECODE" not in env
        assert "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        # 用户自己配置的鉴权变量不清理
        assert env["ANTHROPIC_API_KEY"] == "test-key-kept"

    def test_env_untouched_outside_host_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不在宿主会话里时不清理用户的 ANTHROPIC_BASE_URL。"""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_CHILD_SESSION", raising=False)
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://my-gateway.invalid")
        assert driver.build_env("c1", 1)["ANTHROPIC_BASE_URL"] == "https://my-gateway.invalid"

    def test_refuses_when_not_logged_in(self) -> None:
        """CLI 未登录时在启动任何步骤前报错。"""

        def _runner(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, stdout='{"loggedIn": false}', stderr="")

        with pytest.raises(ValueError, match="auth login"):
            driver.ensure_logged_in("claude", {}, _runner)

    def test_parse_result(self) -> None:
        """解析 claude JSON 输出；非 JSON 视为错误。"""
        ok = driver.parse_result(
            '{"type":"result","is_error":false,"total_cost_usd":0.4,"session_id":"x"}'
        )
        assert ok["total_cost_usd"] == 0.4
        assert driver.parse_result("boom")["is_error"] is True

    def test_refuses_nested_driver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """战役模式内不能再启动驱动器。"""
        monkeypatch.setenv("QUANT_CAMPAIGN_ID", "c1")
        with pytest.raises(ValueError, match="嵌套"):
            driver.run_campaign("c1", driver.DriverConfig())

    def test_loop_runs_until_stop(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """主循环：取任务卡 → 启动 agent → 收口，直到 next_task 返回 stop。"""
        monkeypatch.delenv("QUANT_CAMPAIGN_ID", raising=False)
        monkeypatch.setattr(driver, "REPO_ROOT", tmp_path)
        tasks = [
            {
                "action": "run",
                "card": {
                    "campaign_id": "c1",
                    "workspace": "candidates/c1/",
                    "next": {"role": "researcher", "round": 1},
                },
            },
            {"action": "stop", "reason": "达到轮数上限"},
        ]
        fake = MagicMock()
        fake.next_task.side_effect = tasks
        fake.require.return_value = SimpleNamespace(model=None, max_step_usd=2.0, status="stopped")
        fake.begin_step.return_value = {"step_id": "abcdef1234"}
        fake.finalize_step.return_value = {"status": "done", "outcome": "rejected", "cost_usd": 0.5}
        monkeypatch.setattr(driver, "ResearchCampaignService", lambda db: fake)
        calls: list[list[str]] = []

        def _runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[1:] == ["auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout='{"loggedIn": true}', stderr="")
            calls.append(cmd)
            assert kwargs["env"]["QUANT_CAMPAIGN_ID"] == "c1"
            assert "任务卡" in kwargs["input"]
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"is_error": false, "total_cost_usd": 0.5, "session_id": "s"}',
                stderr="",
            )

        result = driver.run_campaign(
            "c1",
            driver.DriverConfig(),
            runner=_runner,
            session_factory=MagicMock,
            echo=lambda _msg: None,
        )
        assert result["steps_run"] == 1
        assert result["stop_reason"] == "达到轮数上限"
        assert "--max-budget-usd" in calls[0] and "dontAsk" in calls[0]
        # 不合并仓库的项目 / 本地权限设置，也不加载 MCP 服务
        source_index = calls[0].index("--setting-sources")
        assert calls[0][source_index + 1] == "user"
        assert "--strict-mcp-config" in calls[0]
        assert fake.finalize_step.call_args.kwargs["cost_usd"] == 0.5
        assert (tmp_path / "candidates" / "c1" / "claude-settings.json").exists()
        # 收尾时对已结束战役补一次自动清理
        fake.sweep_finished.assert_called_once_with("c1")
        assert result["cleanup"] is fake.sweep_finished.return_value

    def test_skips_step_log_when_steps_dir_removed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """战役在 agent 进程内结束并清理了 steps/ 时，驱动器不写日志但照常记录花费。"""
        monkeypatch.delenv("QUANT_CAMPAIGN_ID", raising=False)
        monkeypatch.setattr(driver, "REPO_ROOT", tmp_path)
        fake = MagicMock()
        fake.next_task.side_effect = [
            {
                "action": "run",
                "card": {
                    "campaign_id": "c1",
                    "workspace": "candidates/c1/",
                    "next": {"role": "researcher", "round": 1},
                },
            },
            {"action": "stop", "reason": "研究员判定无新方向"},
        ]
        fake.require.return_value = SimpleNamespace(model=None, max_step_usd=None, status="completed")
        fake.begin_step.return_value = {"step_id": "abcdef1234"}
        fake.finalize_step.return_value = {"status": "done", "outcome": None, "cost_usd": 0.7}
        monkeypatch.setattr(driver, "ResearchCampaignService", lambda db: fake)
        steps_dir = tmp_path / "candidates" / "c1" / "steps"

        def _runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[1:] == ["auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout='{"loggedIn": true}', stderr="")
            shutil.rmtree(steps_dir)  # 模拟 agent 进程内 stop() 的清理
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"is_error": false, "total_cost_usd": 0.7}', stderr="err"
            )

        driver.run_campaign(
            "c1",
            driver.DriverConfig(),
            runner=_runner,
            session_factory=MagicMock,
            echo=lambda _msg: None,
        )
        assert not steps_dir.exists()
        kwargs = fake.finalize_step.call_args.kwargs
        assert kwargs["cost_usd"] == 0.7
        assert not kwargs.get("error")


class TestDesktopOrchestration:
    """桌面编排模式：按数据库判定战役模式、登记与收口步骤、给子会话的提示。"""

    @pytest.mark.parametrize(
        ("env", "desktop", "caller", "expected"),
        [
            ("c1", None, None, "c1"),
            (None, None, "s-any", None),
            (None, ("c2", "orch"), "orch", None),
            (None, ("c2", "orch"), "child", "c2"),
            (None, ("c2", "orch"), None, "c2"),
        ],
    )
    def test_resolve_campaign_mode(
        self,
        env: str | None,
        desktop: tuple[str, str] | None,
        caller: str | None,
        expected: str | None,
    ) -> None:
        """环境变量优先；桌面模式下只有编排会话自身不受约束（终端与子会话都受约束）。"""
        assert rules.resolve_campaign_mode(env, desktop, caller) == expected

    def test_agents_cannot_orchestrate(self) -> None:
        """子会话不能登记 / 收口步骤（否则可把自己登记为编排者绕过约束）。"""
        for action in ("begin-step", "finalize-step", "stop"):
            assert (
                rules.check_command_allowed("research", "campaign", {"campaign_action": action})
                is not None
            )

    def test_desktop_prompt_states_boundaries(self) -> None:
        """桌面提示写明平台无法强制的边界；headless 提示不带这段。"""
        card = {
            "campaign_id": "c1",
            "workspace": "candidates/c1/",
            "next": {"role": "researcher", "round": 1},
        }
        desktop = rules.build_agent_prompt(card, skill_dir="skills", desktop=True)
        headless = rules.build_agent_prompt(card, skill_dir="skills", desktop=False)
        assert "桌面编排模式的额外边界" in desktop and "git" in desktop
        assert "桌面编排模式" not in headless
        assert "step-done c1" in desktop
        venv = rules.build_agent_prompt(
            card, skill_dir="skills", desktop=True, python_cmd="apps/api/.venv/Scripts/python.exe"
        )
        assert (
            "apps/api/.venv/Scripts/python.exe -m quant_etf_api.cli research campaign step-done c1"
            in venv
        )
        assert "不是项目虚拟环境" in venv and "不是项目虚拟环境" not in desktop

    def test_resolve_mode_uses_desktop_step(self) -> None:
        """服务按运行中的桌面步骤判定战役模式。"""
        svc = _service(_campaign())
        svc._repo.find_desktop_running_step.return_value = _step(orchestrator_session_id="orch")
        assert svc.resolve_mode(None, "child") == "c1"
        assert svc.resolve_mode(None, "orch") is None
        assert svc.resolve_mode("c9", "orch") == "c9"
        svc._repo.find_desktop_running_step.return_value = None
        assert svc.resolve_mode(None, "child") is None

    def test_begin_desktop_step(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """登记步骤时记下编排会话，并返回给子会话的提示与工作目录。"""
        monkeypatch.setattr(
            "quant_etf_api.services.research_campaign_service.utcnow_aware",
            lambda: _NOW + timedelta(hours=1),
        )
        monkeypatch.setattr("quant_etf_api.services.research_campaign_service._REPO_ROOT", tmp_path)
        svc = _service(_campaign())
        with pytest.raises(ValueError, match="编排会话"):
            svc.begin_desktop_step("c1", "")
        result = svc.begin_desktop_step("c1", "orch")
        assert result["action"] == "run"
        added = svc._repo.add.call_args.args[0]
        assert added.orchestrator_session_id == "orch"
        assert result["step"]["orchestrator_session_id"] == "orch"
        assert "桌面编排模式的额外边界" in result["prompt"]
        assert (tmp_path / "candidates" / "c1").is_dir()

    def test_begin_desktop_step_passes_stop_through(self) -> None:
        """战役已结束时不登记步骤，直接返回停止原因。"""
        svc = _service(_campaign(status="stopped", stop_reason="达到轮数上限"))
        assert svc.begin_desktop_step("c1", "orch") == {
            "action": "stop",
            "reason": "达到轮数上限",
        }
        svc._repo.add.assert_not_called()

    def test_finalize_desktop_step(self) -> None:
        """子会话未回报判失败；已回报的步骤只补记会话 ID。"""
        running = _step()
        svc = _service(_campaign(), [running])
        svc._repo.find_step.return_value = running
        assert svc.finalize_desktop_step("c1", agent_session_id="local_x")["status"] == "failed"
        assert running.agent_session_id == "local_x"

        done = _step(status="done", outcome="rejected")
        svc = _service(_campaign(), [done])
        svc._repo.find_step.return_value = done
        result = svc.finalize_desktop_step("c1", agent_session_id="local_y")
        assert result["status"] == "done"
        assert result["agent_session_id"] == "local_y"


class TestCliCampaignMode:
    """CLI 入口的战役模式判定。"""

    def _patch(self, monkeypatch: pytest.MonkeyPatch, mode: str | None) -> MagicMock:
        """替换 CLI 内的数据库会话与战役服务。"""
        import quant_etf_api.cli as cli

        fake = MagicMock()
        fake.resolve_mode.return_value = mode
        monkeypatch.setattr(cli, "SessionLocal", MagicMock)
        monkeypatch.setattr(cli, "ResearchCampaignService", lambda db: fake)
        monkeypatch.delenv("QUANT_CAMPAIGN_ID", raising=False)
        return fake

    def test_desktop_mode_guards_child_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """子会话调用被拦截，判定结果写回 args 供假设登记使用。"""
        import quant_etf_api.cli as cli

        fake = self._patch(monkeypatch, "c2")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "child")
        args = cli._build_parser().parse_args(["research", "ledger"])
        cli._guard_campaign_mode(args)
        fake.resolve_mode.assert_called_once_with(None, "child")
        fake.guard_command.assert_called_once()
        assert cli._campaign_mode(args) == "c2"

    def test_terminal_can_stop_stuck_campaign(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不在任何 Claude Code 会话里的终端可以停止卡住的桌面战役。"""
        import quant_etf_api.cli as cli

        fake = self._patch(monkeypatch, "c2")
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        args = cli._build_parser().parse_args(
            ["research", "campaign", "stop", "c2", "--reason", "编排会话丢失"]
        )
        cli._guard_campaign_mode(args)
        fake.guard_command.assert_not_called()

    def test_unresolvable_mode_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """判定失败（如数据库不可用）时拒绝执行。"""
        import quant_etf_api.cli as cli

        fake = self._patch(monkeypatch, None)
        fake.resolve_mode.side_effect = RuntimeError("db down")
        args = cli._build_parser().parse_args(["research", "ledger"])
        with pytest.raises(SystemExit):
            cli._guard_campaign_mode(args)

    def test_orchestration_commands_registered(self) -> None:
        """begin-step / finalize-step 已注册。"""
        import quant_etf_api.cli as cli

        parser = cli._build_parser()
        begun = parser.parse_args(
            ["research", "campaign", "begin-step", "c1", "--orchestrator-session", "o"]
        )
        assert begun.orchestrator_session == "o"
        done = parser.parse_args(
            ["research", "campaign", "finalize-step", "c1", "--agent-session", "a", "--error", "e"]
        )
        assert (done.agent_session, done.error) == ("a", "e")


class TestCampaignRepositorySql:
    """战役仓库查询的 PostgreSQL 方言编译校验。"""

    def test_has_review_only_counts_running_or_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """has_review 只认 running / done 步骤（失败步骤的结论不算）。"""
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.orm import Query, Session

        from quant_etf_api.infra.db.repositories.research_campaign import (
            ResearchCampaignRepository,
        )

        captured: list[Any] = []

        def _first(query: Query) -> None:
            captured.append(query.statement)
            return None

        monkeypatch.setattr(Query, "first", _first)
        repo = ResearchCampaignRepository(Session())
        assert repo.has_review("c1", "o1", "recommend_accept") is False
        compiled = captured[0].compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "research_campaign_step.status IN" in sql
        values = [v for v in compiled.params.values() if isinstance(v, (list, tuple))]
        assert ["running", "done"] in [list(v) for v in values]


class TestCampaignRejectsComposite:
    """战役模式不支持组合策略。"""

    def test_guard_rejects_composite_strategy_and_file(self, tmp_path: Path) -> None:
        """组合策略 ID 与组合策略配置文件都被拒绝。"""
        svc = _service(_campaign())
        svc._config_svc.is_composite.return_value = True
        with pytest.raises(ValueError, match="组合策略"):
            svc.guard_command("c1", "research", {"subcommand": "batch", "strategy_id": "base"})
        svc._config_svc.is_composite.return_value = False
        path = tmp_path / "c.json"
        path.write_text(
            json.dumps({"kind": "composite", "members": [{"strategy_id": "a", "weight": 1}]}),
            encoding="utf-8",
        )
        flags = {
            "subcommand": "create",
            "lineage": "base",
            "draft": True,
            "file": str(path),
            "strategy_id": "base__x",
        }
        with pytest.raises(ValueError, match="组合策略"):
            svc.guard_command("c1", "strategy", flags)
