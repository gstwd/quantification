"""研究台账仓库：研究假设与研究试验的查询与持久化。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from quant_etf_api.domain.research.ledger import COUNTED_TRIAL_KINDS
from quant_etf_api.infra.db.models.core import (
    ResearchHypothesisModel,
    ResearchTrialModel,
    RobustnessRunModel,
    StrategyOptimizationModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository

# 已评估（产生过回测结果）的优化会话状态
_EVALUATED_OPTIMIZATION_STATUSES: tuple[str, ...] = ("evaluated", "accepted", "rejected")


class ResearchLedgerRepository(BaseRepository):
    """research_hypothesis / research_trial 表的查询仓库（事务由调用方管理）。"""

    def find_hypothesis(self, hypothesis_id: str) -> ResearchHypothesisModel | None:
        """按主键查询假设。

        Args:
            hypothesis_id: 假设 ID。

        Returns:
            假设行，不存在返回 None。
        """
        return self._db.get(ResearchHypothesisModel, hypothesis_id)

    def add(self, model: ResearchHypothesisModel | ResearchTrialModel) -> None:
        """把假设或试验行加入会话（不提交）。

        Args:
            model: ORM 行。
        """
        self._db.add(model)

    def list_hypotheses(
        self,
        *,
        strategy_id: str | None = None,
        index_code: str | None = None,
        tag: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 200,
    ) -> list[ResearchHypothesisModel]:
        """按条件查询假设，按登记时间倒序。

        Args:
            strategy_id: 关联策略 ID 过滤。
            index_code: 指数代码过滤（``index_codes`` 包含该代码）。
            tag: 标签过滤（``tags`` 包含该标签）。
            statuses: 状态过滤列表。
            limit: 最大返回条数。

        Returns:
            假设行列表。
        """
        query = self._db.query(ResearchHypothesisModel)
        if strategy_id:
            query = query.filter(ResearchHypothesisModel.strategy_id == strategy_id)
        if index_code:
            query = query.filter(ResearchHypothesisModel.index_codes.contains([index_code]))
        if tag:
            query = query.filter(ResearchHypothesisModel.tags.contains([tag]))
        if statuses:
            query = query.filter(ResearchHypothesisModel.status.in_(statuses))
        return query.order_by(ResearchHypothesisModel.created_at.desc()).limit(limit).all()

    def list_children(self, hypothesis_id: str) -> list[ResearchHypothesisModel]:
        """查询直接子假设，按登记时间升序。

        Args:
            hypothesis_id: 父假设 ID。

        Returns:
            子假设行列表。
        """
        return (
            self._db.query(ResearchHypothesisModel)
            .filter(ResearchHypothesisModel.parent_id == hypothesis_id)
            .order_by(ResearchHypothesisModel.created_at)
            .all()
        )

    def list_trials(
        self,
        *,
        hypothesis_id: str | None = None,
        strategy_id: str | None = None,
        limit: int = 200,
    ) -> list[ResearchTrialModel]:
        """按条件查询试验，按记录时间倒序。

        Args:
            hypothesis_id: 所属假设 ID 过滤。
            strategy_id: 策略 ID 过滤。
            limit: 最大返回条数。

        Returns:
            试验行列表。
        """
        query = self._db.query(ResearchTrialModel)
        if hypothesis_id:
            query = query.filter(ResearchTrialModel.hypothesis_id == hypothesis_id)
        if strategy_id:
            query = query.filter(ResearchTrialModel.strategy_id == strategy_id)
        return query.order_by(ResearchTrialModel.created_at.desc()).limit(limit).all()

    def has_diagnostic(self, fingerprint: str) -> bool:
        """是否已有该派生因子指纹的诊断记录。

        Args:
            fingerprint: 派生因子指纹。

        Returns:
            存在时为 True。
        """
        row = (
            self._db.query(ResearchTrialModel.trial_id)
            .filter(
                ResearchTrialModel.kind == "diagnostic",
                ResearchTrialModel.ref_id == fingerprint,
            )
            .first()
        )
        return row is not None

    def trial_counts_by_hypothesis(self, hypothesis_ids: list[str]) -> dict[str, int]:
        """统计每个假设下挂的试验行数。

        Args:
            hypothesis_ids: 假设 ID 列表。

        Returns:
            {hypothesis_id: 试验行数}，没有试验的假设不出现。
        """
        if not hypothesis_ids:
            return {}
        rows = (
            self._db.query(ResearchTrialModel.hypothesis_id, sa.func.count())
            .filter(ResearchTrialModel.hypothesis_id.in_(hypothesis_ids))
            .group_by(ResearchTrialModel.hypothesis_id)
            .all()
        )
        return {str(hid): int(count) for hid, count in rows}

    def sum_robustness_trials(self, strategy_ids: Sequence[str]) -> int:
        """累加给定策略（通常为一条研究线的全部成员）所有稳健性批次的变体数。

        Args:
            strategy_ids: 策略 ID 列表。

        Returns:
            变体总数。
        """
        total = (
            self._db.query(sa.func.coalesce(sa.func.sum(RobustnessRunModel.trial_count), 0))
            .filter(RobustnessRunModel.strategy_id.in_(list(strategy_ids)))
            .scalar()
        )
        return int(total or 0)

    def count_evaluated_optimizations(self, strategy_ids: Sequence[str]) -> int:
        """统计以给定策略为基线、已评估过的优化会话数。

        Args:
            strategy_ids: 基线策略 ID 列表。

        Returns:
            会话数。
        """
        total = (
            self._db.query(sa.func.count())
            .select_from(StrategyOptimizationModel)
            .filter(
                StrategyOptimizationModel.strategy_id.in_(list(strategy_ids)),
                StrategyOptimizationModel.status.in_(_EVALUATED_OPTIMIZATION_STATUSES),
            )
            .scalar()
        )
        return int(total or 0)

    def sum_ledger_trials(self, strategy_ids: Sequence[str]) -> dict[tuple[str, bool], int]:
        """按（来源, 是否估算）累加台账中计入 N 的试验次数。

        Args:
            strategy_ids: 策略 ID 列表。

        Returns:
            {(kind, is_estimated): 试验次数}，只含 ``COUNTED_TRIAL_KINDS``。
        """
        rows = (
            self._db.query(
                ResearchTrialModel.kind,
                ResearchTrialModel.is_estimated,
                sa.func.coalesce(sa.func.sum(ResearchTrialModel.trial_count), 0),
            )
            .filter(
                ResearchTrialModel.strategy_id.in_(list(strategy_ids)),
                ResearchTrialModel.kind.in_(COUNTED_TRIAL_KINDS),
            )
            .group_by(ResearchTrialModel.kind, ResearchTrialModel.is_estimated)
            .all()
        )
        return {(str(kind), bool(estimated)): int(total or 0) for kind, estimated, total in rows}

    def find_evaluated_optimizations(
        self, strategy_ids: Sequence[str] | None = None
    ) -> list[StrategyOptimizationModel]:
        """查询已评估（产生过全区间回测）的优化会话，按创建时间升序。

        Args:
            strategy_ids: 基线策略 ID 列表；None 表示全部策略。

        Returns:
            会话行列表。
        """
        query = self._db.query(StrategyOptimizationModel).filter(
            StrategyOptimizationModel.status.in_(_EVALUATED_OPTIMIZATION_STATUSES)
        )
        if strategy_ids is not None:
            query = query.filter(StrategyOptimizationModel.strategy_id.in_(list(strategy_ids)))
        return query.order_by(StrategyOptimizationModel.created_at.asc()).all()

    def find_successful_robustness_runs(
        self, strategy_ids: Sequence[str] | None = None
    ) -> list[RobustnessRunModel]:
        """查询已成功完成的稳健性批次，按创建时间升序。

        Args:
            strategy_ids: 基线策略 ID 列表；None 表示全部策略。

        Returns:
            批次行列表。
        """
        query = self._db.query(RobustnessRunModel).filter(RobustnessRunModel.status == "success")
        if strategy_ids is not None:
            query = query.filter(RobustnessRunModel.strategy_id.in_(list(strategy_ids)))
        return query.order_by(RobustnessRunModel.created_at.asc()).all()
