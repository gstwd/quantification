"""研究战役服务：自动研究循环的状态、下一步任务卡、步骤记录、评审与命令拦截。

驱动器（``campaign_driver``）每一步调用 ``next_task`` 拿到任务卡，启动一个全新的
headless agent 会话执行，结束后 ``finalize_step`` 收口。agent 在会话内通过 CLI 回报
（``step_done`` / ``review``），并受 ``guard_command`` 约束。

规则（下一步是谁、何时停、哪些命令放行）放在 ``domain.research.campaign``；
本服务只做读写编排。
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.research import campaign as rules
from quant_etf_api.engine.config import is_composite_config
from quant_etf_api.infra.db.models.core import ResearchCampaignModel, ResearchCampaignStepModel
from quant_etf_api.infra.db.repositories.optimization import OptimizationRepository
from quant_etf_api.infra.db.repositories.research_campaign import ResearchCampaignRepository
from quant_etf_api.infra.time import utcnow_aware
from quant_etf_api.services.research_ledger_service import ResearchLedgerService
from quant_etf_api.services.strategy_config_service import StrategyConfigService

logger = logging.getLogger(__name__)

# 仓库根目录（本文件位于 apps/api/src/quant_etf_api/services/）：战役工作目录在其下
_REPO_ROOT = Path(__file__).resolve().parents[5]
# 任务卡里附带的最近步骤条数
_RECENT_STEPS = 6
# 需要 --strategy 落在战役研究线内的命令
_STRATEGY_SCOPED: frozenset[tuple[str, str]] = frozenset(
    {
        ("research", "batch"),
        ("robustness", "scan"),
        ("robustness", "ablate"),
        ("robustness", "pool"),
        ("optimization", "start"),
        ("backtest", "run"),
    }
)


class ResearchCampaignService:
    """研究战役的读写编排服务。"""

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: 数据库会话。
        """
        self._db = db
        self._repo = ResearchCampaignRepository(db)
        self._ledger = ResearchLedgerService(db)
        self._config_svc = StrategyConfigService(db)
        self._opt_repo = OptimizationRepository(db)

    # ------------------------------------------------------------------
    # 战役生命周期
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        objective: str,
        strategy_id: str,
        max_trials: int,
        max_hours: float,
        max_rounds: int,
        max_consecutive_rejects: int,
        index_codes: Sequence[str] | None = None,
        max_diagnostics: int = rules.DEFAULT_MAX_DIAGNOSTICS,
        max_step_usd: float | None = None,
        model: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """开启研究战役。

        Args:
            objective: 研究目标。
            strategy_id: 种子基线策略 ID（必须已存在）。
            max_trials: 本战役最多新增的试验次数。
            max_hours: 墙钟时长上限。
            max_rounds: 研究员轮数上限。
            max_consecutive_rejects: 连续否决上限。
            index_codes: 研究范围指数。
            max_diagnostics: 战役内诊断命令次数上限。
            max_step_usd: 单步 agent 花费上限。
            model: agent 模型。
            created_by: 发起者。

        Returns:
            战役字典。

        Raises:
            ValueError: 参数非法或种子策略不存在。
        """
        text = (objective or "").strip()
        if not text:
            raise ValueError("研究目标不能为空")
        if self._config_svc.get_config(strategy_id) is None:
            raise ValueError(f"种子策略 {strategy_id} 不存在")
        for name, value in (
            ("max_trials", max_trials),
            ("max_rounds", max_rounds),
            ("max_consecutive_rejects", max_consecutive_rejects),
            ("max_diagnostics", max_diagnostics),
        ):
            if value < 1:
                raise ValueError(f"{name} 必须 ≥ 1")
        if max_hours <= 0:
            raise ValueError("max_hours 必须 > 0")
        budget = self._ledger.budget_status(strategy_id)
        now = utcnow_aware()
        model_row = ResearchCampaignModel(
            campaign_id=f"c{uuid4().hex[:12]}",
            objective=text,
            strategy_id=strategy_id,
            lineage_id=budget["lineage_id"],
            index_codes=list(index_codes) if index_codes else None,
            status=rules.STATUS_RUNNING,
            max_trials=int(max_trials),
            max_hours=float(max_hours),
            max_rounds=int(max_rounds),
            max_consecutive_rejects=int(max_consecutive_rejects),
            max_diagnostics=int(max_diagnostics),
            diagnostics_used=0,
            max_step_usd=max_step_usd,
            model=model,
            trials_at_start=int(budget["used"]),
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self._repo.add(model_row)
        self._db.commit()
        logger.info("研究战役 %s 开始：%s", model_row.campaign_id, strategy_id)
        return self._campaign_dict(model_row)

    def require(self, campaign_id: str) -> ResearchCampaignModel:
        """读取战役，不存在时报错。

        Args:
            campaign_id: 战役 ID。

        Returns:
            战役行。

        Raises:
            ValueError: 战役不存在。
        """
        row = self._repo.find(campaign_id)
        if row is None:
            raise ValueError(f"研究战役 {campaign_id} 不存在")
        return row

    def stop(
        self, campaign_id: str, reason: str, *, status: str = rules.STATUS_STOPPED
    ) -> dict[str, Any]:
        """停止战役（运行中的步骤标记为失败）。

        Args:
            campaign_id: 战役 ID。
            reason: 停止原因。
            status: 终态（stopped / completed）。

        Returns:
            战役字典。
        """
        row = self.require(campaign_id)
        if row.status == rules.STATUS_RUNNING:
            now = utcnow_aware()
            running = self._repo.find_running_step(campaign_id)
            if running is not None:
                running.status = rules.STEP_FAILED
                running.error = f"战役停止：{reason}"
                running.finished_at = now
            row.status = status
            row.stop_reason = reason
            row.finished_at = now
            row.updated_at = now
            self._db.commit()
            logger.info("研究战役 %s 结束（%s）：%s", campaign_id, status, reason)
        return self._campaign_dict(row)

    # ------------------------------------------------------------------
    # 下一步与步骤
    # ------------------------------------------------------------------

    def progress(self, campaign_id: str) -> dict[str, Any]:
        """汇总战役进度（限额使用情况与下一步安排）。

        Args:
            campaign_id: 战役 ID。

        Returns:
            {"trials_used", "lineage_budget", "elapsed_hours", "rounds_done",
            "consecutive_rejects", "consecutive_failures", "next", "stop_reason"}。
        """
        row = self.require(campaign_id)
        steps = self._repo.list_steps(campaign_id)
        views = [_step_view(step) for step in steps]
        budget = self._ledger.budget_status(row.strategy_id)
        closed = [
            h.status
            for h in sorted(
                (h for h in self._repo.list_hypotheses(campaign_id) if h.closed_at is not None),
                key=lambda h: h.closed_at,
            )
        ]
        next_step = rules.plan_next_step(views)
        elapsed = (utcnow_aware() - _aware(row.created_at)).total_seconds() / 3600
        trials_used = max(0, int(budget["used"]) - int(row.trials_at_start))
        rounds_done = sum(
            1
            for step in views
            if step.role == rules.ROLE_RESEARCHER and step.status == rules.STEP_DONE
        )
        consecutive_rejects = rules.count_consecutive_rejects(closed)
        consecutive_failures = rules.count_consecutive_failures(views)
        stop_reason = rules.evaluate_stop(
            trials_used=trials_used,
            max_trials=row.max_trials,
            lineage_remaining=int(budget["remaining"]),
            elapsed_hours=elapsed,
            max_hours=row.max_hours,
            rounds_done=rounds_done,
            max_rounds=row.max_rounds,
            consecutive_rejects=consecutive_rejects,
            max_consecutive_rejects=row.max_consecutive_rejects,
            consecutive_failures=consecutive_failures,
            next_is_new_round=(
                next_step is not None
                and next_step.role == rules.ROLE_RESEARCHER
                and not next_step.retry
            ),
        )
        return {
            "trials_used": trials_used,
            "max_trials": row.max_trials,
            "lineage_budget": budget,
            "elapsed_hours": round(elapsed, 3),
            "max_hours": row.max_hours,
            "rounds_done": rounds_done,
            "max_rounds": row.max_rounds,
            "consecutive_rejects": consecutive_rejects,
            "max_consecutive_rejects": row.max_consecutive_rejects,
            "consecutive_failures": consecutive_failures,
            "diagnostics_used": int(row.diagnostics_used or 0),
            "max_diagnostics": int(row.max_diagnostics or rules.DEFAULT_MAX_DIAGNOSTICS),
            "next": None if next_step is None else next_step.__dict__,
            "stop_reason": stop_reason,
        }

    def next_task(self, campaign_id: str) -> dict[str, Any]:
        """生成下一步任务卡；触顶时直接停止战役。

        Args:
            campaign_id: 战役 ID。

        Returns:
            ``{"action": "stop", "reason"}``、``{"action": "wait", "step_id"}``
            或 ``{"action": "run", "card": {...}}``。
        """
        row = self.require(campaign_id)
        if row.status != rules.STATUS_RUNNING:
            return {"action": "stop", "reason": row.stop_reason or row.status}
        running = self._repo.find_running_step(campaign_id)
        if running is not None:
            return {"action": "wait", "step_id": running.step_id, "role": running.role}
        progress = self.progress(campaign_id)
        if progress["stop_reason"]:
            self.stop(campaign_id, progress["stop_reason"])
            return {"action": "stop", "reason": progress["stop_reason"]}
        return {"action": "run", "card": self._task_card(row, progress)}

    def begin_step(
        self,
        campaign_id: str,
        *,
        role: str,
        round_no: int,
        agent_session_id: str | None = None,
        optimization_id: str | None = None,
        hypothesis_id: str | None = None,
        orchestrator_session_id: str | None = None,
    ) -> dict[str, Any]:
        """登记一个开始运行的步骤（驱动器 / 桌面编排会话在启动 agent 前调用）。

        Args:
            campaign_id: 战役 ID。
            role: 角色。
            round_no: 轮次。
            agent_session_id: 预先分配的 agent 会话 ID。
            optimization_id: 评审对象（评审员）。
            hypothesis_id: 相关假设。
            orchestrator_session_id: 桌面编排模式下编排会话的 ID（headless 模式为 None）。

        Returns:
            步骤字典。

        Raises:
            ValueError: 战役未在运行、已有运行中步骤，或另一个战役正在运行。
        """
        row = self.require(campaign_id)
        if row.status != rules.STATUS_RUNNING:
            raise ValueError(f"战役 {campaign_id} 已结束（{row.status}）")
        if role not in rules.ROLES:
            raise ValueError(f"未知角色 {role!r}")
        if self._repo.find_running_step(campaign_id) is not None:
            raise ValueError(f"战役 {campaign_id} 已有运行中的步骤")
        others = self._repo.other_active_campaigns(campaign_id)
        if others:
            raise ValueError(f"战役 {', '.join(others)} 正在运行，同一时间只允许一个战役")
        step = ResearchCampaignStepModel(
            step_id=uuid4().hex,
            campaign_id=campaign_id,
            round=int(round_no),
            role=role,
            status=rules.STEP_RUNNING,
            agent_session_id=agent_session_id,
            optimization_id=optimization_id,
            hypothesis_id=hypothesis_id,
            orchestrator_session_id=orchestrator_session_id,
            started_at=utcnow_aware(),
        )
        self._repo.add(step)
        self._db.commit()
        return _step_dict(step)

    def begin_desktop_step(self, campaign_id: str, orchestrator_session_id: str) -> dict[str, Any]:
        """桌面编排模式：取下一张任务卡并登记步骤，返回给子会话的提示。

        编排会话（Claude Code 桌面会话）调用后，用返回的 ``prompt`` 新开一个桌面会话
        执行本步。步骤运行期间，除编排会话外的 CLI 调用都按数据库进入战役模式。

        Args:
            campaign_id: 战役 ID。
            orchestrator_session_id: 编排会话的 ``CLAUDE_CODE_SESSION_ID``。

        Returns:
            ``{"action": "run", "step", "card", "prompt"}``；战役已停止或触顶时为
            ``{"action": "stop", "reason"}``；有未收口步骤时为 ``{"action": "wait", ...}``。

        Raises:
            ValueError: 缺少编排会话 ID。
        """
        orchestrator = (orchestrator_session_id or "").strip()
        if not orchestrator:
            raise ValueError(
                "桌面编排模式需要编排会话 ID：在 Claude Code 会话内运行（读取 "
                "CLAUDE_CODE_SESSION_ID），或用 --orchestrator-session 显式给出"
            )
        task = self.next_task(campaign_id)
        if task["action"] != "run":
            return task
        card = task["card"]
        plan = card["next"]
        step = self.begin_step(
            campaign_id,
            role=plan["role"],
            round_no=plan["round"],
            optimization_id=plan.get("optimization_id"),
            hypothesis_id=plan.get("hypothesis_id"),
            orchestrator_session_id=orchestrator,
        )
        (_REPO_ROOT / "candidates" / campaign_id).mkdir(parents=True, exist_ok=True)
        return {
            "action": "run",
            "step": step,
            "card": card,
            "prompt": rules.build_agent_prompt(
                card, skill_dir=rules.SKILL_DIR, desktop=True, python_cmd=_python_command()
            ),
        }

    def finalize_desktop_step(
        self,
        campaign_id: str,
        *,
        agent_session_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """桌面编排模式：子会话结束后收口最近一步。

        子会话已回报 step-done 时只补记会话 ID；评审员记了结论但漏了 step-done 视为
        完成；其余未回报的步骤判为失败（下一步原地重试）。

        Args:
            campaign_id: 战役 ID。
            agent_session_id: 执行本步的桌面会话 ID。
            error: 编排侧观察到的错误（如子会话报错、被人工停止）。

        Returns:
            步骤字典。

        Raises:
            ValueError: 战役没有任何步骤。
        """
        self.require(campaign_id)
        step = self._repo.find_running_step(campaign_id)
        if step is None:
            steps = self._repo.list_steps(campaign_id)
            if not steps:
                raise ValueError(f"战役 {campaign_id} 还没有步骤")
            step = steps[-1]
        return self.finalize_step(
            step.step_id,
            exit_code=0 if error is None else -1,
            agent_session_id=agent_session_id,
            error=error,
        )

    def resolve_mode(
        self, env_campaign_id: str | None, caller_session_id: str | None
    ) -> str | None:
        """判定一次 CLI 调用所处的战役模式（环境变量或桌面模式运行中步骤）。

        Args:
            env_campaign_id: 环境变量 ``QUANT_CAMPAIGN_ID``。
            caller_session_id: 调用方的 ``CLAUDE_CODE_SESSION_ID``。

        Returns:
            战役 ID；不在战役模式时为 None。
        """
        if env_campaign_id:
            return env_campaign_id
        step = self._repo.find_desktop_running_step()
        desktop = (
            None
            if step is None or not step.orchestrator_session_id
            else (step.campaign_id, step.orchestrator_session_id)
        )
        return rules.resolve_campaign_mode(None, desktop, caller_session_id)

    def step_done(
        self,
        campaign_id: str,
        *,
        outcome: str | None,
        summary: str,
        hypothesis_id: str | None = None,
        optimization_id: str | None = None,
    ) -> dict[str, Any]:
        """agent 回报本步完成（研究员给 outcome；评审员的结论已由 ``review`` 记录）。

        Args:
            campaign_id: 战役 ID。
            outcome: 研究员回报结果（评审员可省略）。
            summary: 本步结论摘要。
            hypothesis_id: 涉及的假设。
            optimization_id: 提交评审的优化会话（``review_requested`` 时必填）。

        Returns:
            步骤字典。

        Raises:
            ValueError: 没有运行中步骤、结果非法或缺少评审对象。
        """
        step = self._running_step(campaign_id)
        text = (summary or "").strip()
        if not text:
            raise ValueError("step-done 必须写明本步结论摘要（--summary）")
        if step.role == rules.ROLE_REVIEWER:
            if step.outcome is None:
                raise ValueError("评审员须先用 research campaign review 记录评审结论")
        else:
            if outcome is None:
                raise ValueError("研究员必须给出 --outcome")
            rules.validate_outcome(step.role, outcome)
            if outcome == rules.OUTCOME_REVIEW_REQUESTED:
                self._require_reviewable(campaign_id, optimization_id)
            step.outcome = outcome
        if hypothesis_id:
            step.hypothesis_id = hypothesis_id
        if optimization_id:
            step.optimization_id = optimization_id
        step.summary = text
        step.status = rules.STEP_DONE
        step.finished_at = utcnow_aware()
        self._db.commit()
        if step.role == rules.ROLE_RESEARCHER and outcome == rules.OUTCOME_NO_IDEA:
            self.stop(campaign_id, f"研究员判定没有新方向：{text}", status=rules.STATUS_COMPLETED)
        return _step_dict(step)

    def review(
        self, campaign_id: str, *, optimization_id: str, verdict: str, reason: str
    ) -> dict[str, Any]:
        """评审员记录评审结论（推荐接受后才允许 finish --verdict accept）。

        Args:
            campaign_id: 战役 ID。
            optimization_id: 被评审的优化会话 ID。
            verdict: recommend_accept / reject / needs_more。
            reason: 评审理由。

        Returns:
            步骤字典。

        Raises:
            ValueError: 当前步骤不是评审员、结论非法或会话不在战役研究线内。
        """
        step = self._running_step(campaign_id)
        if step.role != rules.ROLE_REVIEWER:
            raise ValueError("只有评审员步骤可以记录评审结论")
        rules.validate_outcome(step.role, verdict)
        text = (reason or "").strip()
        if not text:
            raise ValueError("评审必须写明理由（--reason）")
        self._require_reviewable(campaign_id, optimization_id)
        step.optimization_id = optimization_id
        step.outcome = verdict
        step.summary = text
        self._db.commit()
        return _step_dict(step)

    def finalize_step(
        self,
        step_id: str,
        *,
        exit_code: int,
        cost_usd: float | None = None,
        agent_session_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """驱动器在 agent 会话结束后收口步骤。

        agent 已回报 step-done 时只补记花费；评审员记了结论但忘了 step-done 时
        视为完成；其余未回报的步骤标记为失败（下一步原地重试）。

        Args:
            step_id: 步骤 ID。
            exit_code: agent 进程退出码。
            cost_usd: 本步花费。
            agent_session_id: agent 会话 ID。
            error: 驱动器侧的错误信息。

        Returns:
            步骤字典。
        """
        step = self._repo.find_step(step_id)
        if step is None:
            raise ValueError(f"步骤 {step_id} 不存在")
        if cost_usd is not None:
            step.cost_usd = cost_usd
        if agent_session_id:
            step.agent_session_id = agent_session_id
        if step.status == rules.STEP_RUNNING:
            step.finished_at = utcnow_aware()
            if step.role == rules.ROLE_REVIEWER and step.outcome and exit_code == 0:
                step.status = rules.STEP_DONE
            else:
                step.status = rules.STEP_FAILED
                step.error = error or f"agent 退出（exit={exit_code}）但未回报 step-done"
        self._db.commit()
        return _step_dict(step)

    # ------------------------------------------------------------------
    # 查询与报告
    # ------------------------------------------------------------------

    def status(self, campaign_id: str) -> dict[str, Any]:
        """战役详情：配置、进度、步骤与假设。

        Args:
            campaign_id: 战役 ID。

        Returns:
            战役详情字典。
        """
        row = self.require(campaign_id)
        result = self._campaign_dict(row)
        result["progress"] = self.progress(campaign_id)
        result["steps"] = [_step_dict(step) for step in self._repo.list_steps(campaign_id)]
        result["hypotheses"] = [
            _hypothesis_brief(h) for h in self._repo.list_hypotheses(campaign_id)
        ]
        result["total_cost_usd"] = round(
            sum(step.get("cost_usd") or 0.0 for step in result["steps"]), 4
        )
        return result

    def list_campaigns(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """列出战役。

        Args:
            status: 状态过滤。
            limit: 最大条数。

        Returns:
            战役字典列表。
        """
        return [self._campaign_dict(row) for row in self._repo.list_campaigns(status, limit)]

    def report(self, campaign_id: str) -> str:
        """生成战役报告 Markdown。

        Args:
            campaign_id: 战役 ID。

        Returns:
            报告全文。
        """
        detail = self.status(campaign_id)
        progress = detail["progress"]
        lines = [
            f"# 研究战役报告 {campaign_id}",
            "",
            "> 研究期内的自动研究记录，不构成投资建议；promote 与上线须人工决定。",
            "",
            f"- 目标：{detail['objective']}",
            f"- 种子策略：{detail['strategy_id']}（研究线 {detail['lineage_id']}）",
            f"- 状态：{detail['status']}；停止原因：{detail.get('stop_reason') or '—'}",
            f"- 试验：本战役新增 {progress['trials_used']}/{progress['max_trials']}，"
            f"研究线已用 {progress['lineage_budget']['used']}/{progress['lineage_budget']['budget']}",
            f"- 轮数：{progress['rounds_done']}/{progress['max_rounds']}；"
            f"时长 {progress['elapsed_hours']:.2f}/{progress['max_hours']:g} 小时；"
            f"agent 花费 ${detail['total_cost_usd']:.2f}",
            f"- 诊断命令：{progress['diagnostics_used']}/{progress['max_diagnostics']} 次",
            "",
            "## 假设与结论",
            "",
            "| 假设 | 状态 | 陈述 | 结论 |",
            "| --- | --- | --- | --- |",
        ]
        for h in detail["hypotheses"]:
            lines.append(
                f"| {h['hypothesis_id']} | {h['status']} | {_cell(h['statement'])} | "
                f"{_cell(h.get('conclusion') or '')} |"
            )
        lines += [
            "",
            "## 步骤",
            "",
            "| 轮 | 角色 | 状态 | 结果 | 优化会话 | 摘要 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for step in detail["steps"]:
            lines.append(
                f"| {step['round']} | {step['role']} | {step['status']} | "
                f"{step.get('outcome') or '—'} | {step.get('optimization_id') or '—'} | "
                f"{_cell(step.get('summary') or step.get('error') or '')} |"
            )
        accepted = [
            step["optimization_id"]
            for step in detail["steps"]
            if step["role"] == rules.ROLE_REVIEWER
            and step.get("outcome") == rules.VERDICT_RECOMMEND_ACCEPT
            and step.get("optimization_id")
        ]
        lines += ["", "## 待人工确认", ""]
        if accepted:
            for oid in accepted:
                lines.append(
                    f"- 优化会话 `{oid}`：评审推荐接受。确认后人工执行 promote 或另建策略。"
                )
        else:
            lines.append("- 无")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # 战役模式命令拦截
    # ------------------------------------------------------------------

    def guard_command(self, campaign_id: str, command: str, flags: Mapping[str, Any]) -> None:
        """战役模式下校验一条 CLI 命令（命令白名单 + 研究线范围 + 评审前置 + 诊断配额）。

        诊断命令放行时即计入配额并提交。

        Args:
            campaign_id: 环境变量中的战役 ID。
            command: 命令组。
            flags: 解析后的参数。

        Raises:
            ValueError: 命令被拒绝。
        """
        row = self.require(campaign_id)
        if row.status != rules.STATUS_RUNNING:
            raise ValueError(f"战役 {campaign_id} 已结束（{row.status}），不能再执行命令")
        subcommand = flags.get("subcommand")
        reason = rules.check_command_allowed(command, subcommand, flags)
        if reason:
            raise ValueError(reason)
        target_campaign = flags.get("campaign_id")
        if command == "research" and subcommand == "campaign" and target_campaign:
            if target_campaign != campaign_id:
                raise ValueError(f"战役内只能操作本战役 {campaign_id}（收到 {target_campaign}）")
        members = set(self._config_svc.lineage_members(row.strategy_id))
        strategy_id = flags.get("strategy_id")
        if (command, subcommand) in _STRATEGY_SCOPED and strategy_id not in members:
            raise ValueError(
                f"战役只能检验研究线 {row.lineage_id} 内的策略（{strategy_id} 不在其中）"
            )
        if command == "strategy" and subcommand == "update":
            target_row = self._config_svc.get_config(strategy_id or "")
            if strategy_id not in members or target_row is None or target_row.status != "draft":
                raise ValueError(
                    f"战役只能修改研究线 {row.lineage_id} 内的草稿策略（基线与启用策略不可直接改）"
                )
        output = rules.file_output_target(command, subcommand, flags)
        if output:
            workspace = (Path.cwd() / "candidates" / campaign_id).resolve()
            target_path = Path(output).resolve()
            if workspace != target_path and workspace not in target_path.parents:
                raise ValueError(f"战役内报告只能写到 candidates/{campaign_id}/ 下")
        if command == "strategy" and subcommand == "create":
            lineage = flags.get("lineage")
            if not lineage or self._config_svc.lineage_of(lineage) != row.lineage_id:
                raise ValueError(
                    f"战役内新建的策略必须用 --lineage {row.strategy_id} 归入研究线 {row.lineage_id}"
                )
        if command == "strategy" and subcommand in ("create", "update"):
            self._reject_composite_file(flags.get("file"))
        strategy_id = flags.get("strategy_id")
        if strategy_id and self._config_svc.is_composite(str(strategy_id)):
            raise ValueError("战役模式不支持组合策略：成员可能在研究线外，组合研究由人工进行")
        self._guard_scoped_ids(row, command, subcommand, flags, members)
        if command == "optimization" and subcommand in ("evaluate", "finish"):
            session = self._opt_repo.find_by_id(flags.get("optimization_id") or "")
            if session is not None and session.strategy_id not in members:
                raise ValueError(f"优化会话不在研究线 {row.lineage_id} 内")
            if subcommand == "finish" and flags.get("verdict") == "accept":
                if not self._repo.has_review(
                    campaign_id, flags.get("optimization_id") or "", rules.VERDICT_RECOMMEND_ACCEPT
                ):
                    raise ValueError(
                        "战役内接受优化会话前，评审员必须先用 research campaign review "
                        "给出 recommend_accept"
                    )
        if rules.is_diagnostic_command(command, subcommand):
            quota = rules.check_diagnostic_quota(
                int(row.diagnostics_used or 0),
                int(row.max_diagnostics or rules.DEFAULT_MAX_DIAGNOSTICS),
            )
            if quota:
                raise ValueError(quota)
            # 放行即计数（命令随后失败也算一次），保证配额不被反复失败的命令绕过
            self._repo.increment_diagnostics(campaign_id)
            self._db.commit()

    @staticmethod
    def _reject_composite_file(path: Any) -> None:
        """战役内新建 / 修改策略时拒绝组合策略配置文件。

        Args:
            path: ``--file`` 参数（可为空）。

        Raises:
            ValueError: 配置文件是组合策略时抛出；文件不可读时交给命令本身报错。
        """
        if not path:
            return
        try:
            data = json.loads(Path(str(path)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        config_json = data.get("config_json", data) if isinstance(data, dict) else None
        if is_composite_config(config_json):
            raise ValueError("战役模式不支持组合策略：成员可能在研究线外，组合研究由人工进行")

    def _guard_scoped_ids(
        self,
        row: ResearchCampaignModel,
        command: str,
        subcommand: str | None,
        flags: Mapping[str, Any],
        members: set[str],
    ) -> None:
        """按 ID 操作的命令（批次、回测、假设）只能作用于本战役 / 研究线内的对象。

        Args:
            row: 战役行。
            command: 命令组。
            subcommand: 子命令。
            flags: 解析后的参数。
            members: 研究线成员策略 ID。

        Raises:
            ValueError: 对象不在研究线或不属于本战役。
        """
        robustness_id = flags.get("robustness_id")
        if command == "robustness" and robustness_id:
            owner = self._repo.robustness_strategy(str(robustness_id))
            if owner is not None and owner not in members:
                raise ValueError(f"稳健性批次 {robustness_id} 不在研究线 {row.lineage_id} 内")
        backtest_id = flags.get("backtest_id")
        if command == "backtest" and subcommand == "cancel" and backtest_id:
            owner = self._repo.backtest_strategy(str(backtest_id))
            if owner is not None and owner not in members:
                raise ValueError(f"回测 {backtest_id} 不在研究线 {row.lineage_id} 内")
        if command == "research" and subcommand == "hypothesis":
            action = flags.get("hypothesis_action")
            if action == "add":
                strategy_id = flags.get("strategy_id")
                if strategy_id and strategy_id not in members:
                    raise ValueError(f"战役内登记的假设只能关联研究线 {row.lineage_id} 内的策略")
            elif action in ("close", "reopen"):
                hypothesis_id = str(flags.get("hypothesis_id") or "")
                exists, owner_campaign = self._repo.hypothesis_campaign(hypothesis_id)
                if exists and owner_campaign != row.campaign_id:
                    raise ValueError(
                        f"战役内只能关闭 / 重开本战役登记的假设（{hypothesis_id} 不属于 "
                        f"{row.campaign_id}）"
                    )

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _running_step(self, campaign_id: str) -> ResearchCampaignStepModel:
        """读取运行中步骤，不存在时报错。"""
        self.require(campaign_id)
        step = self._repo.find_running_step(campaign_id)
        if step is None:
            raise ValueError(f"战役 {campaign_id} 没有运行中的步骤")
        return step

    def _require_reviewable(self, campaign_id: str, optimization_id: str | None) -> None:
        """校验优化会话可被评审：存在、已评估、且在战役研究线内。"""
        if not optimization_id:
            raise ValueError("提交 / 记录评审必须给出 --optimization-id")
        session = self._opt_repo.find_by_id(optimization_id)
        if session is None:
            raise ValueError(f"优化会话 {optimization_id} 不存在")
        row = self.require(campaign_id)
        if session.strategy_id not in set(self._config_svc.lineage_members(row.strategy_id)):
            raise ValueError(f"优化会话 {optimization_id} 不在研究线 {row.lineage_id} 内")
        if session.status not in ("evaluated", "accepted", "rejected"):
            raise ValueError(f"优化会话 {optimization_id} 尚未评估完成（{session.status}）")

    def _task_card(self, row: ResearchCampaignModel, progress: dict[str, Any]) -> dict[str, Any]:
        """组装任务卡：角色、上下文、限额与本步要求。"""
        steps = self._repo.list_steps(row.campaign_id)
        last_review = next(
            (
                _step_dict(step)
                for step in reversed(steps)
                if step.role == rules.ROLE_REVIEWER and step.outcome
            ),
            None,
        )
        ledger = self._ledger.ledger(strategy_id=row.strategy_id, recent_closed=10)
        return {
            "campaign_id": row.campaign_id,
            "objective": row.objective,
            "strategy_id": row.strategy_id,
            "lineage_id": row.lineage_id,
            "lineage_members": self._config_svc.lineage_members(row.strategy_id),
            "index_codes": row.index_codes or [],
            "next": progress["next"],
            "limits": {
                key: progress[key]
                for key in (
                    "trials_used",
                    "max_trials",
                    "rounds_done",
                    "max_rounds",
                    "consecutive_rejects",
                    "max_consecutive_rejects",
                    "elapsed_hours",
                    "max_hours",
                    "diagnostics_used",
                    "max_diagnostics",
                )
            },
            "lineage_budget": progress["lineage_budget"],
            "research_period": {
                "start": get_settings().research_period_start,
                "end": get_settings().research_period_end,
            },
            "workspace": f"candidates/{row.campaign_id}/",
            "campaign_hypotheses": [
                _hypothesis_brief(h) for h in self._repo.list_hypotheses(row.campaign_id)
            ],
            "ledger_recent_closed": [
                {
                    "hypothesis_id": h["hypothesis_id"],
                    "status": h["status"],
                    "statement": h["statement"],
                    "conclusion": h["conclusion"],
                }
                for h in ledger.get("recent_closed", [])
            ],
            "last_review": last_review,
            "recent_steps": [_step_dict(step) for step in steps[-_RECENT_STEPS:]],
        }

    @staticmethod
    def _campaign_dict(row: ResearchCampaignModel) -> dict[str, Any]:
        """战役行转字典。"""
        return {
            "campaign_id": row.campaign_id,
            "objective": row.objective,
            "strategy_id": row.strategy_id,
            "lineage_id": row.lineage_id,
            "index_codes": row.index_codes or [],
            "status": row.status,
            "max_trials": row.max_trials,
            "max_hours": row.max_hours,
            "max_rounds": row.max_rounds,
            "max_consecutive_rejects": row.max_consecutive_rejects,
            "max_diagnostics": int(row.max_diagnostics or rules.DEFAULT_MAX_DIAGNOSTICS),
            "diagnostics_used": int(row.diagnostics_used or 0),
            "max_step_usd": row.max_step_usd,
            "model": row.model,
            "trials_at_start": row.trials_at_start,
            "stop_reason": row.stop_reason,
            "created_by": row.created_by,
            "created_at": _iso(row.created_at),
            "finished_at": _iso(row.finished_at),
        }


def _python_command() -> str:
    """子会话运行平台 CLI 的解释器：沿用编排者当前进程的解释器（项目虚拟环境）。

    桌面子会话的 PATH 上未必是装了项目依赖的 python；仓库内的路径写成相对仓库根目录的
    正斜杠形式，便于在提示里直接使用。

    Returns:
        解释器命令。
    """
    executable = Path(sys.executable).resolve()
    try:
        return executable.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return executable.as_posix()


def _aware(value: datetime) -> datetime:
    """把可能缺时区的时间戳按 UTC 解释。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    """时间戳转 ISO 字符串。"""
    return value.isoformat() if value is not None else None


def _cell(text: str, limit: int = 160) -> str:
    """Markdown 表格单元格：去换行、转义竖线并截断。"""
    flat = " ".join(str(text).split()).replace("|", "\\|")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _step_view(step: ResearchCampaignStepModel) -> rules.StepView:
    """步骤行转规则视图。"""
    return rules.StepView(
        round=step.round,
        role=step.role,
        status=step.status,
        outcome=step.outcome,
        optimization_id=step.optimization_id,
        hypothesis_id=step.hypothesis_id,
    )


def _step_dict(step: ResearchCampaignStepModel) -> dict[str, Any]:
    """步骤行转字典。"""
    return {
        "step_id": step.step_id,
        "round": step.round,
        "role": step.role,
        "status": step.status,
        "outcome": step.outcome,
        "hypothesis_id": step.hypothesis_id,
        "optimization_id": step.optimization_id,
        "summary": step.summary,
        "error": step.error,
        "cost_usd": step.cost_usd,
        "agent_session_id": step.agent_session_id,
        "orchestrator_session_id": step.orchestrator_session_id,
        "started_at": _iso(step.started_at),
        "finished_at": _iso(step.finished_at),
    }


def _hypothesis_brief(h: Any) -> dict[str, Any]:
    """战役内假设的简要字典。"""
    return {
        "hypothesis_id": h.hypothesis_id,
        "status": h.status,
        "category": h.category,
        "statement": h.statement,
        "pre_registration": h.pre_registration,
        "conclusion": h.conclusion,
    }
