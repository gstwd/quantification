from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import ResearchRunModel
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.schemas.run import ResearchRunSummary

logger = logging.getLogger(__name__)


class RunService:
    def __init__(self, db: Session, run_repo: ResearchRunRepository | None = None) -> None:
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
        self, run_type: str, strategy_id: str | None, trade_date: date
    ) -> ResearchRunSummary:
        run_id = str(uuid4())
        now = datetime.now(timezone.utc)
        try:
            run = ResearchRunModel(
                run_id=run_id,
                run_type=run_type,
                strategy_id=strategy_id,
                trade_date=trade_date,
                status="pending",
                started_at=now,
            )
            self._db.add(run)
            self._db.commit()
        except Exception:
            self._db.rollback()
            logger.warning("create_run DB insert failed", exc_info=True)

        # 无论 DB 写入是否成功，都返回 pending 状态的摘要（异步执行场景）
        return ResearchRunSummary(
            run_id=run_id,
            run_type=run_type,
            strategy_id=strategy_id,
            trade_date=trade_date,
            status="pending",
            started_at=now,
        )

    def mark_success(self, run_id: str) -> None:
        """将指定运行标记为成功状态。

        Args:
            run_id: 运行 ID
        """
        try:
            self._run_repo.mark_success(run_id)
        except Exception:
            logger.warning("mark_success 更新失败", exc_info=True)

    def mark_failed(self, run_id: str, error_message: str) -> None:
        """将指定运行标记为失败状态。

        Args:
            run_id: 运行 ID
            error_message: 错误描述
        """
        try:
            self._run_repo.mark_failed(run_id, error_message)
        except Exception:
            logger.warning("mark_failed 更新失败", exc_info=True)
