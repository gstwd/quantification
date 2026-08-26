"""回测仓库。"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, text

from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import (
    BacktestComparisonModel,
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

        使用裸 SQL 直接写入，不触发 ORM flush/commit，
        避免打断主事务中的 daily/index result 写入。

        Args:
            backtest_id: 回测标识。
            progress: 进度百分比（0-100）。
        """
        p = max(0, min(100, progress))
        try:
            self._db.connection().execute(
                text("UPDATE backtest_run SET progress = :p WHERE backtest_id = :bid"),
                {"p": p, "bid": backtest_id},
            )
        except Exception:
            # 静默失败：进度写入不是关键路径，不应打断回测主循环
            pass

    def find_latest_daily_date(self, backtest_id: str) -> date | None:
        """查询回测已保存的最新每日结果日期。

        回测按 checkpoint 分段提交后，中途失败时用此方法定位
        已持久化的部分结果截止日期，写入失败信息便于用户判断进度。

        Args:
            backtest_id: 回测标识。

        Returns:
            最新已提交的每日结果日期，无结果时返回 None。
        """
        return (
            self._db.query(func.max(BacktestDailyResultModel.trade_date))
            .filter(BacktestDailyResultModel.backtest_id == backtest_id)
            .scalar()
        )

    def mark_success(
        self,
        backtest_id: str,
        metrics: dict[str, Any] | None = None,
        warnings: list[dict[str, Any]] | None = None,
    ) -> None:
        """将回测标记为成功，可附带结构化提示。

        Args:
            backtest_id: 回测标识。
            metrics: 汇总绩效指标。
            warnings: 执行过程中的结构化提示列表（BacktestWarning 的 dict 形式）。
        """
        # 如果 session 处于 pending rollback 状态，先回滚以恢复可用状态
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(backtest_id)
        if run is None:
            return
        run.status = "success"
        run.finished_at = utcnow()
        run.progress = 100
        if metrics:
            run.metrics = metrics
        if warnings is not None:
            run.warnings = warnings
        self._db.commit()

    def mark_running(self, backtest_id: str) -> None:
        """将回测标记为执行中状态。

        Args:
            backtest_id: 回测标识。
        """
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(backtest_id)
        if run is None:
            return
        run.status = "running"
        run.started_at = utcnow()
        self._db.commit()

    def add_daily_result(self, row: BacktestDailyResultModel) -> None:
        """登记一条每日组合结果（不提交，由主循环 checkpoint 统一提交）。

        Args:
            row: 每日结果 ORM 行。
        """
        self._db.add(row)

    def add_index_result(self, row: BacktestIndexResultModel) -> None:
        """登记一条指数级信号/收益结果（不提交，由主循环 checkpoint 统一提交）。

        Args:
            row: 指数结果 ORM 行。
        """
        self._db.add(row)

    def mark_failed(
        self,
        backtest_id: str,
        error_message: str,
        warnings: list[dict[str, Any]] | None = None,
    ) -> None:
        """将回测标记为失败，可附带结构化提示。

        Args:
            backtest_id: 回测标识。
            error_message: 失败原因。
            warnings: 执行过程中的结构化提示列表（如 PARTIAL_RESULT）。
        """
        # 如果 session 处于 pending rollback 状态，先回滚以恢复可用状态
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(backtest_id)
        if run is None:
            return
        run.status = "failed"
        run.finished_at = utcnow()
        run.error_message = error_message[:1000]
        if warnings is not None:
            run.warnings = warnings
        self._db.commit()

    # ── 对比回测查询与状态更新 ────────────────────────────────────────────

    def find_all_comparisons(
        self, offset: int = 0, limit: int = 50
    ) -> tuple[list[BacktestComparisonModel], int]:
        """分页查询对比回测记录，按创建时间倒序。

        Args:
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        base_q = self._db.query(BacktestComparisonModel)
        total = base_q.count()
        rows = (
            base_q.order_by(BacktestComparisonModel.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total

    def find_comparison_by_id(self, comparison_id: str) -> BacktestComparisonModel | None:
        """按主键查询对比回测记录。"""
        return self._db.get(BacktestComparisonModel, comparison_id)

    def update_comparison_progress(self, comparison_id: str, progress: int) -> None:
        """更新对比回测执行进度（0-100）。

        使用裸 SQL 直接写入，避免与 ORM 状态冲突。
        """
        p = max(0, min(100, progress))
        try:
            self._db.connection().execute(
                text("UPDATE backtest_comparison SET progress = :p WHERE comparison_id = :cid"),
                {"p": p, "cid": comparison_id},
            )
        except Exception:
            pass

    def mark_comparison_success(
        self, comparison_id: str, metrics: dict[str, Any] | None = None
    ) -> None:
        """将对比回测标记为成功。"""
        if self._db.is_active is False:
            self._db.rollback()
        comp = self.find_comparison_by_id(comparison_id)
        if comp is None:
            return
        comp.status = "success"
        comp.finished_at = utcnow()
        comp.progress = 100
        if metrics:
            comp.comparison_metrics = metrics
        self._db.commit()

    def mark_comparison_failed(self, comparison_id: str, error_message: str) -> None:
        """将对比回测标记为失败（两个子回测均失败）。"""
        if self._db.is_active is False:
            self._db.rollback()
        comp = self.find_comparison_by_id(comparison_id)
        if comp is None:
            return
        comp.status = "failed"
        comp.finished_at = utcnow()
        comp.error_message = error_message[:1000]
        self._db.commit()

    def mark_comparison_partial(self, comparison_id: str, error_message: str) -> None:
        """将对比回测标记为部分成功（一个子回测成功，一个失败）。"""
        if self._db.is_active is False:
            self._db.rollback()
        comp = self.find_comparison_by_id(comparison_id)
        if comp is None:
            return
        comp.status = "partial"
        comp.finished_at = utcnow()
        comp.error_message = error_message[:1000]
        self._db.commit()
