"""回测仓库。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, text

from quant_etf_api.infra.db.models.core import (
    BacktestComparisonModel,
    BacktestDailyResultModel,
    BacktestIndexResultModel,
    BacktestRunModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository
from quant_etf_api.infra.time import utcnow_aware


def calendar_source_filter(value: str) -> Any:
    """构造"调仓日历来源等于指定值"的过滤表达式（C1）。

    ``backtest_run.params`` 在 ORM 中声明为通用 ``JSON``，其比较器没有
    PostgreSQL 专有的 ``astext``，因此用 ``->>`` 操作符取文本值；该表达式
    与列的真实类型 jsonb 无关，json/jsonb 都支持。

    Args:
        value: 目标来源（upstream / database / not_required）。

    Returns:
        可直接用于 ``Query.filter()`` 的 SQL 表达式。
    """
    return BacktestRunModel.params.op("->>")("_calendar_source") == value


class BacktestRepository(BaseRepository):
    """回测相关表的查询与状态更新仓库。"""

    def find_all(
        self,
        offset: int = 0,
        limit: int = 50,
        strategy_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        status: str | None = None,
        purpose: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
        calendar_source: str | None = None,
    ) -> tuple[list[BacktestRunModel], int]:
        """分页查询回测记录，支持状态/用途/时间范围/日历来源筛选（B4/C1）。

        Args:
            offset: 偏移量。
            limit: 每页最大条数。
            strategy_id: 策略 ID，精确匹配。
            created_from: 创建时间起点（UTC aware，含）。
            created_to: 创建时间终点（UTC aware，不含）。
            status: 回测状态，精确匹配（pending/running/success/failed/cancelled）。
            purpose: 回测用途（research/validation/monitor）。
            order_by: 排序字段，可选 created_at / started_at / finished_at。
            descending: 是否倒序。
            calendar_source: 调仓日历来源过滤（upstream/database/not_required），
                用于找出"调仓日历口径不同"的存量回测（C1）。

        Returns:
            (items, total) 元组。
        """
        base_q = self._db.query(BacktestRunModel)
        if strategy_id:
            base_q = base_q.filter(BacktestRunModel.strategy_id == strategy_id)
        if status:
            base_q = base_q.filter(BacktestRunModel.status == status)
        if purpose:
            base_q = base_q.filter(BacktestRunModel.purpose == purpose)
        if calendar_source:
            # 口径指纹存放在 params JSONB 的 _calendar_source 键中
            base_q = base_q.filter(calendar_source_filter(calendar_source))
        if created_from is not None:
            base_q = base_q.filter(BacktestRunModel.created_at >= created_from)
        if created_to is not None:
            base_q = base_q.filter(BacktestRunModel.created_at < created_to)
        total = base_q.count()
        column = {
            "started_at": BacktestRunModel.started_at,
            "finished_at": BacktestRunModel.finished_at,
        }.get(order_by, BacktestRunModel.created_at)
        order = column.desc() if descending else column.asc()
        rows = base_q.order_by(order).offset(offset).limit(limit).all()
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

    def find_many_by_ids(self, backtest_ids: list[str]) -> dict[str, BacktestRunModel]:
        """按主键批量查询回测记录。

        Args:
            backtest_ids: 回测 ID 列表。

        Returns:
            backtest_id → ORM 行 的字典；不存在的 ID 不出现。
        """
        if not backtest_ids:
            return {}
        rows = (
            self._db.query(BacktestRunModel)
            .filter(BacktestRunModel.backtest_id.in_(list(backtest_ids)))
            .all()
        )
        return {row.backtest_id: row for row in rows}

    def delete(self, backtest_id: str) -> bool:
        """删除回测主记录（外键负责级联清理日结果与置空外部引用）。

        Args:
            backtest_id: 回测标识。

        Returns:
            True 表示确实删除了 1 行；False 表示记录不存在。
        """
        deleted = (
            self._db.query(BacktestRunModel)
            .filter(BacktestRunModel.backtest_id == backtest_id)
            .delete(synchronize_session=False)
        )
        self._db.commit()
        return bool(deleted)

    def find_daily_results(self, backtest_id: str) -> list[BacktestDailyResultModel]:
        """查询回测的每日组合结果（按日期升序）。"""
        return (
            self._db.query(BacktestDailyResultModel)
            .filter(BacktestDailyResultModel.backtest_id == backtest_id)
            .order_by(BacktestDailyResultModel.trade_date.asc())
            .all()
        )

    def find_return_turnover_pairs(
        self, backtest_ids: list[str]
    ) -> dict[str, list[tuple[float | None, float | None, float | None]]]:
        """批量读取多条回测的 (组合日收益, 单边换手率, 基准日收益) 序列（F-12）。

        列表页需要净口径指标，但净口径必须由逐日序列现算（C3 的设计：
        不落库、不重跑）。这里只取三列且不构造 ORM 对象，避免为几十条
        十年回测实例化十万级对象；带基准是为了让列表的净超额与
        ``backtest show`` 口径一致。

        Args:
            backtest_ids: 回测标识列表。

        Returns:
            backtest_id → [(portfolio_return, turnover, benchmark_return)]，按交易日升序；
            没有逐日结果时为空字典。
        """
        if not backtest_ids:
            return {}
        stmt = (
            sa.select(
                BacktestDailyResultModel.backtest_id,
                BacktestDailyResultModel.portfolio_return,
                BacktestDailyResultModel.turnover,
                BacktestDailyResultModel.benchmark_return,
            )
            .where(BacktestDailyResultModel.backtest_id.in_(backtest_ids))
            .order_by(
                BacktestDailyResultModel.backtest_id.asc(),
                BacktestDailyResultModel.trade_date.asc(),
            )
        )
        result: dict[str, list[tuple[float | None, float | None, float | None]]] = {}
        for backtest_id, portfolio_return, turnover, benchmark_return in self._db.execute(stmt).all():
            result.setdefault(backtest_id, []).append(
                (portfolio_return, turnover, benchmark_return)
            )
        return result

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
        candidate_pool: dict[str, Any] | None = None,
    ) -> None:
        """将回测标记为成功，可附带结构化提示与候选池时间线。

        Args:
            backtest_id: 回测标识。
            metrics: 汇总绩效指标。
            warnings: 执行过程中的结构化提示列表（BacktestWarning 的 dict 形式）。
            candidate_pool: 有效候选池时间线（C6），None 表示不写入。
        """
        # 如果 session 处于 pending rollback 状态，先回滚以恢复可用状态
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(backtest_id)
        if run is None:
            return
        run.status = "success"
        run.finished_at = utcnow_aware()
        run.progress = 100
        if metrics:
            run.metrics = metrics
        if warnings is not None:
            run.warnings = warnings
        if candidate_pool is not None:
            run.candidate_pool = candidate_pool
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
        run.started_at = utcnow_aware()
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
        run.finished_at = utcnow_aware()
        run.error_message = error_message[:1000]
        if warnings is not None:
            run.warnings = warnings
        self._db.commit()

    def mark_cancelled(
        self,
        backtest_id: str,
        error_message: str = "回测已按请求取消",
        warnings: list[dict[str, Any]] | None = None,
    ) -> None:
        """将回测标记为已取消（B2 协作取消的落库出口）。

        Args:
            backtest_id: 回测标识。
            error_message: 取消原因。
            warnings: 可选的结构化提示（如 CANCELLED）。
        """
        if self._db.is_active is False:
            self._db.rollback()
        run = self.find_by_id(backtest_id)
        if run is None:
            return
        run.status = "cancelled"
        run.finished_at = utcnow_aware()
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
        comp.finished_at = utcnow_aware()
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
        comp.finished_at = utcnow_aware()
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
        comp.finished_at = utcnow_aware()
        comp.error_message = error_message[:1000]
        self._db.commit()
