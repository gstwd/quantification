"""回测仓库。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quant_etf_api.infra.db.models.core import (
    BacktestDailyResultModel,
    BacktestIndexResultModel,
    BacktestRunModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository


class BacktestRepository(BaseRepository):
    """回测相关表的查询与状态更新仓库。"""

    def find_all(self, offset: int = 0, limit: int = 50) -> tuple[list[BacktestRunModel], int]:
        """分页查询回测记录，按创建时间倒序。

        Args:
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        base_q = self._db.query(BacktestRunModel)
        total = base_q.count()
        rows = base_q.order_by(BacktestRunModel.created_at.desc()).offset(offset).limit(limit).all()
        return rows, total

    def find_recent(self, limit: int = 50) -> list[BacktestRunModel]:
        """获取最近的回测记录。"""
        return (
            self._db.query(BacktestRunModel)
            .order_by(BacktestRunModel.created_at.desc())
            .limit(limit)
            .all()
        )

    def find_by_id(self, backtest_id: str) -> BacktestRunModel | None:
        """按主键查询回测记录。"""
        return self._db.get(BacktestRunModel, backtest_id)

    def find_daily_results(self, backtest_id: str) -> list[BacktestDailyResultModel]:
        """查询回测的每日组合结果（按日期升序）。"""
        return (
            self._db.query(BacktestDailyResultModel)
            .filter(BacktestDailyResultModel.backtest_id == backtest_id)
            .order_by(BacktestDailyResultModel.trade_date.asc())
            .all()
        )

    def find_index_results(
        self, backtest_id: str, index_code: str | None = None
    ) -> list[BacktestIndexResultModel]:
        """查询回测的每日指数信号与收益（按日期和指数代码升序）。

        Args:
            backtest_id: 回测标识。
            index_code: 可选的指数代码过滤，None 时返回所有指数。

        Returns:
            BacktestIndexResultModel 列表。
        """
        q = self._db.query(BacktestIndexResultModel).filter(
            BacktestIndexResultModel.backtest_id == backtest_id
        )
        if index_code is not None:
            q = q.filter(BacktestIndexResultModel.index_code == index_code)
        return q.order_by(
            BacktestIndexResultModel.trade_date.asc(),
            BacktestIndexResultModel.index_code.asc(),
        ).all()

    def update_progress(self, backtest_id: str, progress: int) -> None:
        """更新回测执行进度（0-100）。

        Args:
            backtest_id: 回测标识。
            progress: 进度百分比（0-100）。
        """
        run = self.find_by_id(backtest_id)
        if run is None:
            return
        run.progress = max(0, min(100, progress))
        try:
            self._db.commit()
        except Exception:
            self._db.rollback()

    def mark_success(self, backtest_id: str, metrics: dict[str, Any] | None = None) -> None:
        """将回测标记为成功。"""
        # 如果 session 处于 pending rollback 状态，先回滚以恢复可用状态
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(backtest_id)
        if run is None:
            return
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        if metrics:
            run.metrics = metrics
        self._db.commit()

    def mark_failed(self, backtest_id: str, error_message: str) -> None:
        """将回测标记为失败。"""
        # 如果 session 处于 pending rollback 状态，先回滚以恢复可用状态
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(backtest_id)
        if run is None:
            return
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = error_message[:1000]
        self._db.commit()
