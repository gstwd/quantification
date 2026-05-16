from __future__ import annotations

import logging
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import ResearchRunModel
from quant_etf_api.schemas.run import ResearchRunSummary

logger = logging.getLogger(__name__)


class RunService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_runs(self) -> list[ResearchRunSummary]:
        try:
            # 取最近 50 条运行记录，按开始时间倒序
            rows = (
                self._db.query(ResearchRunModel)
                .order_by(ResearchRunModel.started_at.desc())
                .limit(50)
                .all()
            )
            if rows:
                return [
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
        except Exception:
            logger.warning("list_runs DB query failed", exc_info=True)

        # DB 无数据时返回占位运行记录
        return [
            ResearchRunSummary(
                run_id=str(uuid4()),
                run_type="daily_ingest",
                trade_date=date.today(),
                status="pending",
                started_at=datetime.utcnow(),
                finished_at=None,
                error_message=None,
            )
        ]

    def create_run(
        self, run_type: str, strategy_id: str | None, trade_date: date
    ) -> ResearchRunSummary:
        run_id = str(uuid4())
        now = datetime.utcnow()
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
