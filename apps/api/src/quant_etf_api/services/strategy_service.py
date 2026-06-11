"""策略服务，提供策略列表、详情和决策管线调用。

重构后使用 StrategyEngine + StrategyConfig 驱动策略执行，
替代旧的 StrategyPlugin + StrategyRegistry 模式。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.schemas.strategy import (
    AllocationResponse,
    StrategyConfigCreate,
    StrategyConfigUpdate,
    StrategyDetail,
    StrategySummary,
    StrategyValidationResult,
)
from quant_etf_api.services.context_builder import ContextBuilder
from quant_etf_api.services.strategy_config_service import StrategyConfigService

logger = logging.getLogger(__name__)


class StrategyService:
    """策略服务，提供策略列表、详情、配置管理和决策管线调用。"""

    def __init__(self, db: Session | None = None) -> None:
        """初始化策略服务。

        Args:
            db: SQLAlchemy Session，配置管理和 run_allocation 需要。
        """
        self._db = db
        self._engine = StrategyEngine()

    def list_strategies(self) -> list[StrategySummary]:
        """返回所有已启用策略的摘要列表。"""
        if self._db is None:
            return []
        svc = StrategyConfigService(self._db)
        return svc.list_configs()

    def get_strategy(self, strategy_id: str) -> StrategyDetail | None:
        """按 ID 获取策略详情。"""
        if self._db is None:
            return None
        svc = StrategyConfigService(self._db)
        return svc.get_config(strategy_id)

    def run_allocation(
        self,
        strategy_id: str,
        params: dict[str, Any] | None = None,
    ) -> AllocationResponse | None:
        """运行资产配置决策管线。

        从 strategy_config 表加载配置，构建上下文，调用引擎执行。

        Args:
            strategy_id: 策略标识。
            params: 策略参数覆盖（暂未使用，预留扩展）。

        Returns:
            AllocationResponse，策略不存在时返回 None。
        """
        if self._db is None:
            logger.warning("run_allocation: 未提供数据库 Session")
            return None

        # 加载策略配置
        config_svc = StrategyConfigService(self._db)
        config = config_svc.get_parsed_config(strategy_id)
        if config is None:
            return None

        # 构建上下文（使用统一 build 方法，由策略配置驱动因子选择）
        builder = ContextBuilder(self._db)
        context = builder.build(config, date.today())

        # 执行引擎
        result = self._engine.run(config, context)

        return AllocationResponse(
            timing=asdict(result.timing) if result.timing else {},
            rankings=[asdict(r) for r in result.rankings],
            plan={
                "positions": result.positions,
                "total_exposure": result.total_exposure,
                "cash_ratio": result.cash_ratio,
                "method": config.portfolio.method if config.portfolio else "signal_only",
            },
            data_date=context.trade_date,
        )

    # ── 配置管理委托 ──────────────────────────────────────────────────────

    def create_config(self, req: StrategyConfigCreate) -> StrategyDetail:
        """创建策略配置。"""
        if self._db is None:
            raise ValueError("未提供数据库 Session")
        svc = StrategyConfigService(self._db)
        return svc.create_config(req)

    def update_config(
        self, strategy_id: str, req: StrategyConfigUpdate
    ) -> StrategyDetail | None:
        """更新策略配置。"""
        if self._db is None:
            return None
        svc = StrategyConfigService(self._db)
        return svc.update_config(strategy_id, req)

    def delete_config(self, strategy_id: str) -> bool:
        """删除策略配置。"""
        if self._db is None:
            return False
        svc = StrategyConfigService(self._db)
        return svc.delete_config(strategy_id)

    def validate_config(self, config_json: dict[str, Any]) -> StrategyValidationResult:
        """校验策略配置。"""
        return StrategyConfigService.validate_config(config_json)
