"""策略实时执行服务，为单日策略运行构建上下文并调用引擎。

重构后使用 StrategyEngine + StrategyConfig 驱动策略执行。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.infra.db.models.core import (
    IndexFactorValueModel,
    IndexSignalModel,
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

        from quant_etf_api.factors.registry import get_default_factor_registry

        self._context_builder = ContextBuilder(db, registry=get_default_factor_registry())

    def execute(
        self,
        config: StrategyConfig,
        trade_date: date,
        run_id: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """执行单日策略信号计算，写入 index_signal 和 index_factor_value。

        Args:
            config: 策略配置。
            trade_date: 运行对应的交易日。
            run_id: 研究运行 ID。
            params: 策略参数覆盖（暂未使用）。
        """
        # 构建上下文（使用统一 build 方法，由策略配置驱动因子选择）
        context = self._context_builder.build(config, trade_date)
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

        # 收集待写入的信号和因子值
        signal_rows: list[dict[str, Any]] = []
        factor_rows: list[dict[str, Any]] = []
        for r in result.strategy_results:
            signal_rows.append({
                "trade_date": r.trade_date,
                "index_code": r.etf_code,
                "strategy_id": r.strategy_id,
                "signal_score": r.signal_score,
                "signal_level": r.signal_level,
                "signal_label": r.signal_label,
                "signal_payload": r.payload,
                "run_id": run_id,
            })
            for fv in r.factor_values:
                raw = fv.get("value")
                num_val: float | None = None
                txt_val: str | None = None
                if isinstance(raw, (int, float)):
                    num_val = float(raw)
                elif raw is not None:
                    txt_val = str(raw)
                factor_rows.append({
                    "trade_date": r.trade_date,
                    "index_code": r.etf_code,
                    "factor_id": fv["factor_id"],
                    "factor_value_numeric": num_val,
                    "factor_value_text": txt_val,
                    "factor_payload": fv.get("payload"),
                    "strategy_id": r.strategy_id,
                })

        # 批量写入信号（重复则跳过，ON CONFLICT DO NOTHING）
        if signal_rows:
            stmt = pg_insert(IndexSignalModel).values(signal_rows)
            stmt = stmt.on_conflict_do_nothing(constraint="uq_index_signal")
            self._db.execute(stmt)

        # 批量写入因子值（重复则跳过，ON CONFLICT DO NOTHING）
        if factor_rows:
            stmt = pg_insert(IndexFactorValueModel).values(factor_rows)
            stmt = stmt.on_conflict_do_nothing(constraint="uq_index_factor_value")
            self._db.execute(stmt)

        signal_count = len(signal_rows)
        factor_count = len(factor_rows)

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
