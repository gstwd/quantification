"""研究数据瘦身与历史留存的仓库。

只承载查询与删除语句，事务由调用方（``RetentionService``）提交。
瘦身只删除 ``backtest_index_result`` 逐指数明细并在 ``backtest_run`` 打标记；
回测汇总、逐日结果、批次、会话与台账不在本仓库的删除范围内。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, or_, select, update

from quant_etf_api.domain.research.retention import (
    SLIMMABLE_STATUSES,
    SessionRef,
    SlimCandidate,
)
from quant_etf_api.infra.db.models.core import (
    BacktestIndexResultModel,
    BacktestRunModel,
    StrategyLifecycleModel,
    StrategyOptimizationModel,
    SystemAlertModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository


class RetentionRepository(BaseRepository):
    """瘦身候选查询、保护集查询与明细删除。"""

    def find_slim_candidates(
        self,
        strategy_ids: Sequence[str],
        *,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> list[SlimCandidate]:
        """查询范围内的回测（按创建时间升序）。

        Args:
            strategy_ids: 研究线成员策略 ID。
            created_from: 创建时间下限（含），战役范围用。
            created_to: 创建时间上限（含），战役范围用。

        Returns:
            瘦身候选列表。
        """
        if not strategy_ids:
            return []
        stmt = select(
            BacktestRunModel.backtest_id,
            BacktestRunModel.strategy_id,
            BacktestRunModel.status,
            BacktestRunModel.purpose,
            BacktestRunModel.optimization_id,
            BacktestRunModel.index_detail_pruned_at,
        ).where(BacktestRunModel.strategy_id.in_(list(strategy_ids)))
        if created_from is not None:
            stmt = stmt.where(BacktestRunModel.created_at >= created_from)
        if created_to is not None:
            stmt = stmt.where(BacktestRunModel.created_at <= created_to)
        stmt = stmt.order_by(BacktestRunModel.created_at.asc(), BacktestRunModel.backtest_id.asc())
        return [
            SlimCandidate(
                backtest_id=row.backtest_id,
                strategy_id=row.strategy_id,
                status=row.status or "",
                purpose=row.purpose or "research",
                optimization_id=row.optimization_id,
                pruned=row.index_detail_pruned_at is not None,
            )
            for row in self._db.execute(stmt).all()
        ]

    def find_lifecycle_backtest_ids(self) -> set[str]:
        """返回被生命周期记录引用的回测 ID（研究期基线与验证期回测）。"""
        rows = self._db.execute(
            select(
                StrategyLifecycleModel.research_backtest_id,
                StrategyLifecycleModel.validation_backtest_id,
            )
        ).all()
        return {backtest_id for row in rows for backtest_id in row if backtest_id}

    def find_sessions(
        self, optimization_ids: Sequence[str], strategy_ids: Sequence[str]
    ) -> list[SessionRef]:
        """查询与范围相关的优化会话。

        既按回测上的 ``optimization_id`` 匹配，也按基线 / 候选策略匹配，
        覆盖未写 ``optimization_id`` 的存量回测。

        Args:
            optimization_ids: 候选回测上出现的优化会话 ID。
            strategy_ids: 研究线成员策略 ID。

        Returns:
            会话引用列表。
        """
        conditions = []
        if optimization_ids:
            conditions.append(StrategyOptimizationModel.optimization_id.in_(list(optimization_ids)))
        if strategy_ids:
            members = list(strategy_ids)
            conditions.append(StrategyOptimizationModel.strategy_id.in_(members))
            conditions.append(StrategyOptimizationModel.candidate_strategy_id.in_(members))
        if not conditions:
            return []
        rows = self._db.execute(
            select(
                StrategyOptimizationModel.optimization_id,
                StrategyOptimizationModel.status,
                StrategyOptimizationModel.baseline_backtest_id,
                StrategyOptimizationModel.candidate_backtest_id,
            ).where(or_(*conditions))
        ).all()
        return [
            SessionRef(
                optimization_id=row.optimization_id,
                status=row.status or "",
                baseline_backtest_id=row.baseline_backtest_id,
                candidate_backtest_id=row.candidate_backtest_id,
            )
            for row in rows
        ]

    def find_latest_research_backtest_ids(self, strategy_ids: Sequence[str]) -> set[str]:
        """返回每个策略最新一条成功研究回测的 ID（PostgreSQL ``DISTINCT ON``）。

        与 ``RobustnessService._most_held_code`` 读取的回测一致，保证稳健性
        pool 的 drop_hold1 变体仍能读到逐指数明细。

        Args:
            strategy_ids: 需要保护的策略 ID（调用方排除稳健性变体）。

        Returns:
            回测 ID 集合。
        """
        if not strategy_ids:
            return set()
        stmt = (
            select(BacktestRunModel.backtest_id)
            .where(
                BacktestRunModel.strategy_id.in_(list(strategy_ids)),
                BacktestRunModel.purpose == "research",
                BacktestRunModel.status == "success",
                BacktestRunModel.index_detail_pruned_at.is_(None),
            )
            .order_by(BacktestRunModel.strategy_id, BacktestRunModel.created_at.desc())
            .distinct(BacktestRunModel.strategy_id)
        )
        return {row[0] for row in self._db.execute(stmt).all()}

    def prune_index_detail(self, backtest_id: str, pruned_at: datetime) -> int | None:
        """删除单条回测的逐指数明细并打瘦身标记（调用方提交）。

        先 ``FOR UPDATE`` 锁住回测行复核状态：期间被重跑、改成非研究用途
        或已被其他进程瘦身时跳过。

        Args:
            backtest_id: 回测 ID。
            pruned_at: 瘦身时间（UTC，timestamptz）。

        Returns:
            删除的明细行数；复核不通过时返回 None。
        """
        row = self._db.execute(
            select(
                BacktestRunModel.status,
                BacktestRunModel.purpose,
                BacktestRunModel.index_detail_pruned_at,
            )
            .where(BacktestRunModel.backtest_id == backtest_id)
            .with_for_update()
        ).one_or_none()
        if (
            row is None
            or row.status not in SLIMMABLE_STATUSES
            or row.purpose != "research"
            or row.index_detail_pruned_at is not None
        ):
            return None
        result = self._db.execute(
            delete(BacktestIndexResultModel).where(
                BacktestIndexResultModel.backtest_id == backtest_id
            )
        )
        self._db.execute(
            update(BacktestRunModel)
            .where(BacktestRunModel.backtest_id == backtest_id)
            .values(index_detail_pruned_at=pruned_at)
        )
        return int(result.rowcount or 0)

    def delete_read_alerts_before(self, cutoff: datetime) -> int:
        """删除早于截止时间的已读告警（未读告警永不删除）。

        Args:
            cutoff: 截止时间（UTC，timestamptz）。

        Returns:
            删除的告警数。
        """
        result = self._db.execute(
            delete(SystemAlertModel).where(
                SystemAlertModel.read_at.is_not(None),
                SystemAlertModel.created_at < cutoff,
            )
        )
        return int(result.rowcount or 0)
