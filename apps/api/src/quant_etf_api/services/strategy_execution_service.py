"""策略实时执行服务，为单日策略运行构建上下文并调用引擎。

重构后使用 StrategyEngine + StrategyConfig 驱动策略执行。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.infra.db.models.core import (
    EtfFactorValueModel,
    EtfSignalModel,
)
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.services.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class StrategyExecutionService:
    """策略实时执行服务。

    从 strategy_config 加载策略配置，构建引擎上下文，
    调用 StrategyEngine 计算信号/因子值，并将结果持久化。
    """

    def __init__(
        self,
        db: Session,
        run_repo: ResearchRunRepository | None = None,
    ) -> None:
        self._db = db
        self._run_repo = run_repo or ResearchRunRepository(db)
        self._engine = StrategyEngine()
        self._context_builder = ContextBuilder(db)

    def execute(
        self,
        config: StrategyConfig,
        trade_date: date,
        run_id: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """执行单日策略信号计算，写入 etf_signal 和 etf_factor_value。

        Args:
            config: 策略配置。
            trade_date: 运行对应的交易日。
            run_id: 研究运行 ID。
            params: 策略参数覆盖（暂未使用）。
        """
        # 构建上下文
        context = self._context_builder.build_live_context(trade_date)
        if not context.universe:
            logger.warning("execute: 无活跃资产，跳过策略运行")
            return

        # 调用引擎执行
        try:
            result = self._engine.run(config, context)
        except Exception:
            logger.exception("策略 %s 执行失败", config.strategy_id)
            self._mark_run_failed(run_id, f"策略 {config.strategy_id} 执行异常")
            return

        # 写入信号和因子值
        signal_count = 0
        factor_count = 0
        for r in result.strategy_results:
            try:
                self._db.add(
                    EtfSignalModel(
                        trade_date=r.trade_date,
                        etf_code=r.etf_code,
                        strategy_id=r.strategy_id,
                        signal_score=r.signal_score,
                        signal_level=r.signal_level,
                        signal_label=r.signal_label,
                        signal_payload=r.payload,
                        run_id=run_id,
                    )
                )
                signal_count += 1
            except Exception:
                self._db.rollback()
                logger.warning("写入信号失败: %s %s", r.etf_code, r.strategy_id)

            for fv in r.factor_values:
                try:
                    self._db.add(
                        EtfFactorValueModel(
                            trade_date=r.trade_date,
                            etf_code=r.etf_code,
                            factor_id=fv["factor_id"],
                            factor_value_numeric=fv.get("value"),
                            factor_value_text=fv.get("text"),
                            factor_payload=fv.get("payload"),
                            strategy_id=r.strategy_id,
                        )
                    )
                    factor_count += 1
                except Exception:
                    self._db.rollback()
                    logger.warning("写入因子值失败: %s %s %s", r.etf_code, fv["factor_id"])

        self._db.commit()

        # 更新运行状态
        asset_count = len(context.universe)
        self._run_repo.mark_success(
            run_id,
            metrics={
                "etf_count": asset_count,
                "signal_count": signal_count,
                "factor_count": factor_count,
            },
        )
        logger.info(
            "策略执行完成: %s signals=%d factors=%d",
            config.strategy_id, signal_count, factor_count
        )

    def _mark_run_failed(self, run_id: str, message: str) -> None:
        """标记运行失败。"""
        try:
            self._run_repo.mark_failed(run_id, message)
        except Exception:
            logger.warning("更新失败状态时出错", exc_info=True)
