"""研究数据瘦身与历史留存服务。

日常与战役结束后的自动清理只做三类事：

- 删除回测的逐指数明细（``backtest_index_result``），并在 ``backtest_run`` 上记
  ``index_detail_pruned_at``；保护集见 ``domain.research.retention``；
- 删除已结束的后台任务记录（战役范围或超过留存天数）与过期的已读告警；
- 战役结束后删除 ``candidates/<战役>/steps/`` 下的 agent 会话日志。

回测汇总、逐日结果、稳健性批次、优化会话、研究台账、战役记录与草稿策略一律保留，
试验次数 N、验收清单与 DSR 校准的输入不受影响。
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.research.retention import SlimPlan, plan_index_detail_slimming
from quant_etf_api.infra.db.repositories.research_campaign import ResearchCampaignRepository
from quant_etf_api.infra.db.repositories.retention import RetentionRepository
from quant_etf_api.infra.db.repositories.strategy_config import StrategyConfigRepository
from quant_etf_api.infra.job_queue.queue import backtest_job_key
from quant_etf_api.infra.job_queue.repository import JobRepository
from quant_etf_api.infra.time import utcnow_aware
from quant_etf_api.schemas.backtest import BacktestDetailSlimFailure, BacktestDetailSlimResult

logger = logging.getLogger(__name__)

_CAMPAIGN_ID_RE = re.compile(r"^c[0-9a-f]{12}$")

_SLIM_NOTE = (
    "瘦身只删除逐指数明细（归因与逐指数结果不再可用）；回测汇总、逐日结果、稳健性批次、"
    "优化会话、研究台账与草稿策略保留，试验次数 N 与验收清单不变。PostgreSQL 删除后的空间"
    "由 autovacuum 复用，不会立即缩小数据库文件。"
)


class RetentionService:
    """研究数据瘦身与历史留存。"""

    def __init__(self, db: Session, job_repo: JobRepository | None = None) -> None:
        """初始化服务。

        Args:
            db: 数据库会话。
            job_repo: 任务仓库（默认使用全局 SessionLocal）。
        """
        self._db = db
        self._repo = RetentionRepository(db)
        self._strategy_repo = StrategyConfigRepository(db)
        self._campaign_repo = ResearchCampaignRepository(db)
        self._job_repo = job_repo or JobRepository()

    # ── 逐指数明细瘦身 ───────────────────────────────────────────

    def slim_backtest_details(
        self,
        *,
        strategy_id: str | None = None,
        campaign_id: str | None = None,
        dry_run: bool = True,
        max_backtests: int | None = None,
    ) -> BacktestDetailSlimResult | None:
        """删除研究线（或某次已结束战役期间）回测的逐指数明细。

        策略范围取策略所在研究线的全部成员；战役范围取战役研究线，并限定在
        战役创建到结束之间创建的回测。每条回测单独提交，单条失败回滚后记录并继续。

        Args:
            strategy_id: 策略 ID（与 ``campaign_id`` 二选一）。
            campaign_id: 战役 ID（与 ``strategy_id`` 二选一）。
            dry_run: True 时只统计不写库。
            max_backtests: 本次最多瘦身的回测数，None 表示全部。

        Returns:
            瘦身结果；策略不存在时返回 None。

        Raises:
            ValueError: 范围参数不合法、战役不存在或仍在运行。
        """
        if (strategy_id is None) == (campaign_id is None):
            raise ValueError("strategy_id 与 campaign_id 必须且只能指定一个")
        created_from = None
        created_to = None
        if strategy_id is not None:
            row = self._strategy_repo.find_by_id(strategy_id)
            if row is None:
                return None
            lineage_id = getattr(row, "lineage_id", None) or row.strategy_id
            scope: Literal["lineage", "campaign"] = "lineage"
            scope_id = strategy_id
        else:
            campaign = self._campaign_repo.find(str(campaign_id))
            if campaign is None:
                raise ValueError(f"战役不存在：{campaign_id}")
            if campaign.status == "running" or campaign.finished_at is None:
                raise ValueError(f"战役 {campaign_id} 仍在运行，结束后再瘦身")
            lineage_id = campaign.lineage_id
            created_from = campaign.created_at
            created_to = campaign.finished_at
            scope = "campaign"
            scope_id = str(campaign_id)

        member_rows = self._strategy_repo.find_lineage_members(lineage_id)
        members = [r.strategy_id for r in member_rows]
        if strategy_id is not None and strategy_id not in members:
            members.append(strategy_id)
        if lineage_id not in members:
            members.append(lineage_id)
        protect_latest = [
            r.strategy_id for r in member_rows if not getattr(r, "is_variant", False)
        ] or [lineage_id]

        plan = self._plan(members, protect_latest, created_from, created_to)
        result = BacktestDetailSlimResult(
            scope=scope,
            scope_id=scope_id,
            lineage_id=lineage_id,
            strategy_count=len(members),
            dry_run=dry_run,
            total_backtests=plan.total,
            already_pruned=plan.already_pruned,
            protected=dict(plan.protected),
            eligible_backtests=len(plan.eligible),
            remaining_backtests=len(plan.eligible),
            note=_SLIM_NOTE,
        )
        if dry_run:
            return result

        batch = plan.eligible if max_backtests is None else plan.eligible[:max_backtests]
        slimmed = 0
        rows_deleted = 0
        failed: list[BacktestDetailSlimFailure] = []
        for backtest_id in batch:
            try:
                deleted = self._repo.prune_index_detail(backtest_id, utcnow_aware())
                self._db.commit()
            except Exception as exc:  # noqa: BLE001 — 单条失败不影响其余回测
                self._db.rollback()
                logger.warning("回测 %s 瘦身失败: %s", backtest_id, exc)
                failed.append(BacktestDetailSlimFailure(backtest_id=backtest_id, error=str(exc)))
                continue
            if deleted is not None:
                slimmed += 1
                rows_deleted += deleted
        result.slimmed_backtests = slimmed
        result.index_rows_deleted = rows_deleted
        result.failed = failed
        result.remaining_backtests = max(0, len(plan.eligible) - len(batch))
        return result

    def _plan(
        self,
        members: list[str],
        protect_latest: list[str],
        created_from: Any,
        created_to: Any,
    ) -> SlimPlan:
        """查询候选与保护集并生成瘦身计划。"""
        candidates = self._repo.find_slim_candidates(
            members, created_from=created_from, created_to=created_to
        )
        optimization_ids = sorted({c.optimization_id for c in candidates if c.optimization_id})
        return plan_index_detail_slimming(
            candidates,
            lifecycle_ids=self._repo.find_lifecycle_backtest_ids(),
            sessions=self._repo.find_sessions(optimization_ids, members),
            latest_research_ids=self._repo.find_latest_research_backtest_ids(protect_latest),
        )

    # ── 战役结束清理 ─────────────────────────────────────────────

    def cleanup_after_campaign(self, campaign_id: str, workspace: Path) -> dict[str, Any]:
        """战役结束后的自动清理：已结束任务 → 步骤日志 → 逐指数明细。

        按代价从小到大执行，每一步独立捕获异常并回滚，整体从不抛出，
        保证清理失败不影响战役停止。可重复执行（已清理的部分不再计数）。

        Args:
            campaign_id: 战役 ID。
            workspace: 战役工作目录（``candidates/<战役>``）。

        Returns:
            ``jobs_deleted`` / ``step_logs_deleted`` / ``slim`` / ``errors``。
        """
        summary: dict[str, Any] = {
            "jobs_deleted": 0,
            "step_logs_deleted": 0,
            "slim": None,
            "errors": [],
        }
        campaign = None
        try:
            campaign = self._campaign_repo.find(campaign_id)
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            summary["errors"].append(f"读取战役失败: {exc}")
        if campaign is None:
            if not summary["errors"]:
                summary["errors"].append(f"战役不存在：{campaign_id}")
            return summary

        try:
            members = [
                r.strategy_id for r in self._strategy_repo.find_lineage_members(campaign.lineage_id)
            ]
            if campaign.lineage_id not in members:
                members.append(campaign.lineage_id)
            candidates = self._repo.find_slim_candidates(
                members, created_from=campaign.created_at, created_to=campaign.finished_at
            )
            keys = [backtest_job_key(c.backtest_id) for c in candidates]
            summary["jobs_deleted"] = self._job_repo.delete_finished_by_keys(keys)
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            summary["errors"].append(f"清理任务记录失败: {exc}")

        try:
            summary["step_logs_deleted"] = self._delete_step_logs(campaign_id, workspace)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"清理步骤日志失败: {exc}")

        try:
            slim = self.slim_backtest_details(campaign_id=campaign_id, dry_run=False)
            summary["slim"] = slim.model_dump() if slim is not None else None
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            summary["errors"].append(f"瘦身逐指数明细失败: {exc}")
        return summary

    @staticmethod
    def _delete_step_logs(campaign_id: str, workspace: Path) -> int:
        """删除战役工作目录下的 ``steps/``（agent 会话日志），返回删除的文件数。

        只删除真实目录（不跟随符号链接），且战役 ID 必须是平台生成的格式；
        ``report.md``、候选配置与 ``claude-settings.json`` 不在 ``steps/`` 下，保留。
        """
        if not _CAMPAIGN_ID_RE.match(campaign_id) or workspace.name != campaign_id:
            raise ValueError(f"战役工作目录不合法：{workspace}")
        steps = workspace / "steps"
        if steps.is_symlink() or not steps.is_dir():
            return 0
        count = sum(1 for path in steps.rglob("*") if path.is_file())
        shutil.rmtree(steps)
        return count

    # ── 历史留存 ────────────────────────────────────────────────

    def prune_job_history(self, retention_days: int | None = None) -> int:
        """删除超过留存天数的已结束后台任务记录。

        Args:
            retention_days: 留存天数，缺省取 ``job_history_retention_days``。

        Returns:
            删除的任务数。
        """
        days = retention_days or get_settings().job_history_retention_days
        return self._job_repo.delete_finished_before(utcnow_aware() - timedelta(days=days))

    def prune_read_alerts(self, retention_days: int | None = None) -> int:
        """删除超过留存天数的已读告警。

        Args:
            retention_days: 留存天数，缺省取 ``alert_retention_days``。

        Returns:
            删除的告警数。
        """
        days = retention_days or get_settings().alert_retention_days
        try:
            deleted = self._repo.delete_read_alerts_before(utcnow_aware() - timedelta(days=days))
            self._db.commit()
            return deleted
        except Exception:
            self._db.rollback()
            raise
