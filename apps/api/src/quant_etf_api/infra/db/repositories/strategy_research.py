"""按策略清理研究数据的仓库。"""

from __future__ import annotations

from sqlalchemy import func, or_, select

from quant_etf_api.infra.db.models.core import (
    BacktestComparisonModel,
    BacktestDailyResultModel,
    BacktestIndexResultModel,
    BacktestRunModel,
    RobustnessRunModel,
    StrategyConfigModel,
    StrategyOptimizationModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository


class StrategyResearchRepository(BaseRepository):
    """归集并删除策略及其研究草稿产生的数据。"""

    def find_related_draft_strategy_ids(self, strategy_ids: set[str]) -> set[str]:
        """查找由指定策略研究流程生成的 draft 策略。

        覆盖优化会话的候选策略与稳健性批次派生的变体；调用方可重复调用
        本方法以收集多层研究草稿。
        """
        if not strategy_ids:
            return set()
        ids = list(strategy_ids)
        optimization_candidates = (
            self._db.query(StrategyConfigModel.strategy_id)
            .join(
                StrategyOptimizationModel,
                StrategyOptimizationModel.candidate_strategy_id == StrategyConfigModel.strategy_id,
            )
            .filter(
                StrategyOptimizationModel.strategy_id.in_(ids),
                StrategyConfigModel.status == "draft",
            )
            .all()
        )
        robustness_ids = select(RobustnessRunModel.robustness_id).where(
            RobustnessRunModel.strategy_id.in_(ids)
        )
        robustness_variants = (
            self._db.query(StrategyConfigModel.strategy_id)
            .filter(
                StrategyConfigModel.is_variant == True,  # noqa: E712
                StrategyConfigModel.status == "draft",
                StrategyConfigModel.source_batch_id.in_(robustness_ids),
            )
            .all()
        )
        return {row[0] for row in [*optimization_candidates, *robustness_variants]}

    def delete_by_strategies(self, strategy_ids: set[str]) -> dict[str, int]:
        """删除策略集合的回测、对比、稳健性与优化记录，返回各表删除数量。

        回测日结果与指数结果依赖 ``backtest_run`` 的级联删除；在删除前计数，
        使调用方可以准确反馈受影响的记录数。
        """
        ids = list(strategy_ids)
        backtest_ids = select(BacktestRunModel.backtest_id).where(
            BacktestRunModel.strategy_id.in_(ids)
        )
        comparison_filter = or_(
            BacktestComparisonModel.strategy_a_id.in_(ids),
            BacktestComparisonModel.strategy_b_id.in_(ids),
            BacktestComparisonModel.backtest_a_id.in_(backtest_ids),
            BacktestComparisonModel.backtest_b_id.in_(backtest_ids),
        )

        counts = {
            "backtest_daily_result": self._count(
                BacktestDailyResultModel, BacktestDailyResultModel.backtest_id.in_(backtest_ids)
            ),
            "backtest_index_result": self._count(
                BacktestIndexResultModel, BacktestIndexResultModel.backtest_id.in_(backtest_ids)
            ),
            "backtest_comparison": self._count(BacktestComparisonModel, comparison_filter),
            "backtest_run": self._count(BacktestRunModel, BacktestRunModel.strategy_id.in_(ids)),
            "robustness_run": self._count(
                RobustnessRunModel, RobustnessRunModel.strategy_id.in_(ids)
            ),
            "strategy_optimization": self._count(
                StrategyOptimizationModel,
                or_(
                    StrategyOptimizationModel.strategy_id.in_(ids),
                    StrategyOptimizationModel.candidate_strategy_id.in_(ids),
                ),
            ),
        }

        # 先删引用回测的主记录；之后删除回测时，日结果与指数结果由外键级联清理。
        self._db.query(BacktestComparisonModel).filter(comparison_filter).delete(
            synchronize_session=False
        )
        self._db.query(StrategyOptimizationModel).filter(
            or_(
                StrategyOptimizationModel.strategy_id.in_(ids),
                StrategyOptimizationModel.candidate_strategy_id.in_(ids),
            )
        ).delete(synchronize_session=False)
        self._db.query(RobustnessRunModel).filter(RobustnessRunModel.strategy_id.in_(ids)).delete(
            synchronize_session=False
        )
        self._db.query(BacktestRunModel).filter(BacktestRunModel.strategy_id.in_(ids)).delete(
            synchronize_session=False
        )
        return counts

    def delete_draft_strategy_configs(self, strategy_ids: set[str]) -> int:
        """删除已确认关联的 draft 策略配置，绝不删除非 draft 的策略。"""
        if not strategy_ids:
            return 0
        return int(
            self._db.query(StrategyConfigModel)
            .filter(
                StrategyConfigModel.strategy_id.in_(list(strategy_ids)),
                StrategyConfigModel.status == "draft",
            )
            .delete(synchronize_session=False)
        )

    def _count(self, model: type, criterion: object) -> int:
        """统计符合条件的 ORM 表记录。"""
        return int(self._db.query(func.count()).select_from(model).filter(criterion).scalar() or 0)
