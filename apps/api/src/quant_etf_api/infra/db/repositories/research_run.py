"""研究运行仓库。"""

from __future__ import annotations

from typing import Any

from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import ResearchRunItemModel, ResearchRunModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class ResearchRunRepository(BaseRepository):
    """ResearchRunModel 的查询与状态更新仓库。"""

    def find_by_id(self, run_id: str) -> ResearchRunModel | None:
        """按主键查询运行记录。"""
        return self._db.get(ResearchRunModel, run_id)

    def find_all(self, offset: int = 0, limit: int = 50) -> tuple[list[ResearchRunModel], int]:
        """分页查询运行记录，按创建时间倒序。

        Args:
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        base_q = self._db.query(ResearchRunModel)
        total = base_q.count()
        rows = base_q.order_by(ResearchRunModel.started_at.desc()).offset(offset).limit(limit).all()
        return rows, total

    def find_recent(self, limit: int = 50) -> list[ResearchRunModel]:
        """获取最近的运行记录，按创建时间倒序。"""
        return (
            self._db.query(ResearchRunModel)
            .order_by(ResearchRunModel.started_at.desc())
            .limit(limit)
            .all()
        )

    def find_items_by_run_id(self, run_id: str) -> list[ResearchRunItemModel]:
        """查询指定运行的所有子项明细。"""
        return (
            self._db.query(ResearchRunItemModel)
            .filter(ResearchRunItemModel.run_id == run_id)
            .order_by(ResearchRunItemModel.id)
            .all()
        )

    def find_stuck_runs(self) -> list[ResearchRunModel]:
        """查询所有卡在 pending 或 running 状态的运行记录。

        用于进程重启后恢复卡死任务。
        """
        return (
            self._db.query(ResearchRunModel)
            .filter(ResearchRunModel.status.in_(["pending", "running"]))
            .all()
        )

    def mark_running(self, run_id: str) -> None:
        """将运行标记为执行中状态，自动 commit。"""
        # 如果 session 处于 pending rollback 状态，先回滚以恢复可用状态
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(run_id)
        if run is None:
            return
        run.status = "running"
        self._db.commit()

    def mark_success(self, run_id: str, metrics: dict[str, Any] | None = None) -> None:
        """将运行标记为成功并记录指标，自动 commit。"""
        # 如果 session 处于 pending rollback 状态，先回滚以恢复可用状态
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(run_id)
        if run is None:
            return
        run.status = "success"
        run.finished_at = utcnow()
        if metrics:
            run.metrics = metrics
        self._db.commit()

    def mark_failed(self, run_id: str, error_message: str) -> None:
        """将运行标记为失败并记录错误信息，自动 commit。"""
        # 如果 session 处于 pending rollback 状态，先回滚以恢复可用状态
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(run_id)
        if run is None:
            return
        run.status = "failed"
        run.finished_at = utcnow()
        run.error_message = error_message[:1000]
        self._db.commit()
