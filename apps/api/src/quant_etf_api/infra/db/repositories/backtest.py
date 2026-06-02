"""回测仓库。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quant_etf_api.infra.db.models.core import (
    BacktestDailyResultModel,
    BacktestEtfResultModel,
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

    def find_etf_results(
        self, backtest_id: str, etf_code: str | None = None
    ) -> list[BacktestEtfResultModel]:
        """查询回测的单 ETF 结果。"""
        q = self._db.query(BacktestEtfResultModel).filter(
            BacktestEtfResultModel.backtest_id == backtest_id
        )
        if etf_code:
            q = q.filter(BacktestEtfResultModel.etf_code == etf_code)
        return q.order_by(BacktestEtfResultModel.trade_date.asc()).all()

    def mark_success(self, backtest_id: str, metrics: dict[str, Any] | None = None) -> None:
        """将回测标记为成功。"""
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
        run = self.find_by_id(backtest_id)
        if run is None:
            return
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = error_message[:1000]
        self._db.commit()
