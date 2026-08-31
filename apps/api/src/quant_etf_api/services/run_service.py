from __future__ import annotations

import logging
from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import ResearchRunModel
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.schemas.run import ResearchRunDetail, ResearchRunItemSchema, ResearchRunSummary

logger = logging.getLogger(__name__)


class RunService:
    """运行记录服务，管理异步任务的生命周期和状态查询。"""

    def __init__(self, db: Session, run_repo: ResearchRunRepository | None = None) -> None:
        """初始化运行记录服务。

        Args:
            db: 数据库会话。
            run_repo: 运行记录仓库，未提供时自动创建。
        """
        self._db = db
        self._run_repo = run_repo or ResearchRunRepository(db)

    def list_runs(self, offset: int = 0, limit: int = 50) -> tuple[list[ResearchRunSummary], int]:
        """分页查询运行记录。

        Args:
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        try:
            rows, total = self._run_repo.find_all(offset=offset, limit=limit)
            items = [
                ResearchRunSummary(
                    run_id=r.run_id,
                    run_type=r.run_type,
                    strategy_id=r.strategy_id,
                    trade_date=r.trade_date,
                    status=r.status,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                    error_message=r.error_message,
                )
                for r in rows
            ]
            return items, total
        except Exception:
            logger.warning("list_runs DB query failed", exc_info=True)
            return [], 0

    def create_run(
        self,
        run_type: str,
        strategy_id: str | None,
        trade_date: date,
        params: dict | None = None,
    ) -> ResearchRunSummary:
        """创建运行记录。

        Args:
            run_type: 运行类型，如 daily_ingest、strategy_run。
            strategy_id: 关联策略 ID，仅 strategy_run 类型有值。
            trade_date: 运行对应的交易日期。
            params: 运行参数（JSON），如单指数任务携带 index_code。

        Returns:
            新创建的运行记录摘要。

        Raises:
            RuntimeError: 数据库写入失败时抛出。
        """
        run_id = str(uuid4())
        now = utcnow()
        try:
            run = ResearchRunModel(
                run_id=run_id,
                run_type=run_type,
                strategy_id=strategy_id,
                trade_date=trade_date,
                params=params,
                status="pending",
                started_at=now,
            )
            self._db.add(run)
            self._db.commit()
        except Exception:
            self._db.rollback()
            logger.warning("create_run DB insert failed", exc_info=True)
            raise RuntimeError(f"创建运行记录失败: run_type={run_type}") from None

        return ResearchRunSummary(
            run_id=run_id,
            run_type=run_type,
            strategy_id=strategy_id,
            trade_date=trade_date,
            status="pending",
            started_at=now,
        )

    def get_run_detail(self, run_id: str) -> ResearchRunDetail | None:
        """获取运行记录详情，包含 metrics 和耗时。

        Args:
            run_id: 运行 ID。

        Returns:
            运行详情，不存在时返回 None。
        """
        run = self._run_repo.find_by_id(run_id)
        if run is None:
            return None

        duration = None
        if run.started_at and run.finished_at:
            duration = round((run.finished_at - run.started_at).total_seconds(), 1)

        return ResearchRunDetail(
            run_id=run.run_id,
            run_type=run.run_type,
            strategy_id=run.strategy_id,
            trade_date=run.trade_date,
            status=run.status,
            params=run.params,
            metrics=run.metrics,
            error_message=run.error_message,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_seconds=duration,
        )

    def get_run_items(self, run_id: str) -> list[ResearchRunItemSchema]:
        """获取运行的子项明细列表。

        Args:
            run_id: 运行 ID。

        Returns:
            子项明细列表。
        """
        items = self._run_repo.find_items_by_run_id(run_id)
        return [
            ResearchRunItemSchema(
                id=item.id,
                run_id=item.run_id,
                index_code=item.index_code,
                status=item.status,
                message=item.message,
                metrics=item.metrics,
            )
            for item in items
        ]

    def mark_running(self, run_id: str) -> None:
        """将指定运行标记为执行中状态。

        Args:
            run_id: 运行 ID。
        """
        try:
            self._run_repo.mark_running(run_id)
        except Exception:
            logger.warning("mark_running 更新失败", exc_info=True)

    def mark_success(self, run_id: str, metrics: dict | None = None) -> None:
        """将指定运行标记为成功状态。

        Args:
            run_id: 运行 ID。
            metrics: 运行结果指标。
        """
        try:
            self._run_repo.mark_success(run_id, metrics)
        except Exception:
            logger.warning("mark_success 更新失败", exc_info=True)

    def mark_failed(self, run_id: str, error_message: str) -> None:
        """将指定运行标记为失败状态。

        Args:
            run_id: 运行 ID。
            error_message: 错误描述。
        """
        try:
            self._run_repo.mark_failed(run_id, error_message)
        except Exception:
            logger.warning("mark_failed 更新失败", exc_info=True)

    def mark_skipped(self, run_id: str, metrics: dict | None = None) -> None:
        """将指定运行标记为跳过（未执行）状态。

        并发冲突、非交易日等场景使用 skipped 而非 success，
        语义上表示"本次未执行"，避免伪装成成功（对应 P7）。

        Args:
            run_id: 运行 ID。
            metrics: 跳过原因等指标。
        """
        try:
            self._run_repo.mark_skipped(run_id, metrics)
        except Exception:
            logger.warning("mark_skipped 更新失败", exc_info=True)

    def recover_stuck_runs(self) -> int:
        """恢复卡在 pending/running 状态的运行记录。

        进程重启后，之前的后台任务已中断，将这些记录标记为失败。

        Returns:
            恢复的记录数量。
        """
        stuck = self._run_repo.find_stuck_runs()
        count = 0
        for run in stuck:
            try:
                run.status = "failed"
                run.finished_at = utcnow()
                run.error_message = "进程重启，任务中断"
                count += 1
            except Exception:
                logger.warning("恢复卡死任务失败: run_id=%s", run.run_id, exc_info=True)
        if count > 0:
            self._db.commit()
            logger.info("已恢复 %d 个卡死的运行记录", count)
        return count
