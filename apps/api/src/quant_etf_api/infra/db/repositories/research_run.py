"""研究运行仓库。"""

from __future__ import annotations

from datetime import date
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

    def find_latest_successful_by_type_and_date(
        self,
        run_type: str,
        trade_date: date,
    ) -> ResearchRunModel | None:
        """查询指定运行类型与交易日最近一次成功运行。

        用于实时分配补算门控：判断某个交易日是否已经完成过因子计算，
        避免浏览页面反复触发无结果的重复计算。

        Args:
            run_type: 运行类型，如 factor_computation。
            trade_date: 交易日。

        Returns:
            最近一次成功运行记录，不存在时返回 None。
        """
        return (
            self._db.query(ResearchRunModel)
            .filter(
                ResearchRunModel.run_type == run_type,
                ResearchRunModel.trade_date == trade_date,
                ResearchRunModel.status == "success",
            )
            .order_by(
                ResearchRunModel.finished_at.desc().nullslast(),
                ResearchRunModel.started_at.desc(),
            )
            .first()
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

    def mark_partial_success(self, run_id: str, metrics: dict[str, Any] | None = None) -> None:
        """将运行标记为部分成功并记录已完成与失败子项。"""
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(run_id)
        if run is None:
            return
        run.status = "partial_success"
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

    def mark_skipped(self, run_id: str, metrics: dict[str, Any] | None = None) -> None:
        """将运行标记为跳过（未执行）并记录原因，自动 commit。

        skipped 表示"未执行"而非失败：并发冲突、非交易日等场景
        不应伪装成 success 或 failed，语义上对应 P7 的跳过状态。

        Args:
            run_id: 运行记录 ID。
            metrics: 跳过原因等指标。
        """
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(run_id)
        if run is None:
            return
        run.status = "skipped"
        run.finished_at = utcnow()
        if metrics:
            run.metrics = metrics
        self._db.commit()

    def add_item(
        self,
        run_id: str,
        index_code: str,
        status: str,
        message: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """写入一条运行子项明细并立即提交。

        Args:
            run_id: 运行记录 ID。
            index_code: 关联的指数代码。
            status: 子项状态：success/skipped/failed。
            message: 子项消息。
            metrics: 子项指标。
        """
        item = ResearchRunItemModel(
            run_id=run_id,
            index_code=index_code,
            status=status,
            message=message or None,
            metrics=metrics,
        )
        try:
            self._db.add(item)
            self._db.commit()
        except Exception:
            # commit 失败后会话处于回滚待定状态，必须先回滚，
            # 否则同一会话后续任何查询都会抛 PendingRollbackError。
            self._db.rollback()
            raise
