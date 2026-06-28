"""策略服务，提供策略列表、详情和决策管线调用。

使用 StrategyEngine + StrategyConfig 驱动策略执行。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.infra.trading_calendar import TradingCalendar
from quant_etf_api.schemas.strategy import (
    AllocationResponse,
    StarredStrategyItem,
    StarredSummaryResponse,
    StrategyConfigCreate,
    StrategyConfigUpdate,
    StrategyDetail,
    StrategySummary,
    StrategyValidationResult,
)
from quant_etf_api.services.context_builder import ContextBuilder
from quant_etf_api.services.strategy_config_service import StrategyConfigService

logger = logging.getLogger(__name__)


def _resolve_effective_date(trade_date: date | None = None) -> date:
    """将指定日期对齐到最近的交易日。

    当 trade_date 为 None 时，使用今天；若今天为非交易日（周末/节假日），
    则回退至最近一个交易日。确保调仓日判断和分配管线基于真实交易日执行。

    Args:
        trade_date: 指定日期，为 None 时自动对齐。

    Returns:
        对齐后的有效交易日。
    """
    if trade_date is not None:
        return trade_date
    cal = TradingCalendar()
    today = date.today()
    if cal.is_trading_day(today):
        return today
    return cal.latest_trading_day(today)


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
        trade_date: date | None = None,
    ) -> AllocationResponse | None:
        """运行资产配置决策管线。

        从 strategy_config 表加载配置，构建上下文，调用引擎执行。

        Args:
            strategy_id: 策略标识。
            params: 策略参数覆盖（暂未使用，预留扩展）。
            trade_date: 指定交易日，不传则使用今天。

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
        from quant_etf_api.factors.registry import get_default_factor_registry

        builder = ContextBuilder(self._db, registry=get_default_factor_registry())
        effective_date = _resolve_effective_date(trade_date)
        context = builder.build(config, effective_date)

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

    # ── 星标管理 ──────────────────────────────────────────────────────────

    def star_strategy(self, strategy_id: str, is_starred: bool) -> bool:
        """设置策略的星标状态。

        Args:
            strategy_id: 策略标识。
            is_starred: 是否星标。

        Returns:
            是否成功更新，策略不存在时返回 False。
        """
        if self._db is None:
            return False
        svc = StrategyConfigService(self._db)
        result = svc._repo.set_starred(strategy_id, is_starred)
        if result:
            self._db.commit()
        return result

    def get_starred_summary(
        self, trade_date: date | None = None
    ) -> StarredSummaryResponse:
        """获取所有星标策略的当日执行摘要。

        对每个星标策略运行分配管线，判断是否为调仓日，
        返回择时、排名、仓位等摘要信息。

        Args:
            trade_date: 指定交易日，不传则使用今天。

        Returns:
            StarredSummaryResponse，含各策略的执行摘要列表。
        """
        if self._db is None:
            return StarredSummaryResponse(trade_date=trade_date or date.today(), items=[])

        from quant_etf_api.engine.rebalance import DefaultRebalanceScheduler

        effective_date = _resolve_effective_date(trade_date)
        config_svc = StrategyConfigService(self._db)
        starred_rows = config_svc._repo.find_starred()

        items: list[StarredStrategyItem] = []
        scheduler = DefaultRebalanceScheduler()

        for row in starred_rows:
            try:
                config = config_svc.get_parsed_config(row.strategy_id)
                if config is None:
                    continue

                # 判断调仓日（基于交易日对齐后的有效日期）
                if config.rebalance is not None:
                    is_rebalance_day = scheduler.should_rebalance(
                        config.rebalance, effective_date, None
                    )
                else:
                    # 无调仓配置默认视为每日调仓
                    is_rebalance_day = True

                # 运行分配管线
                allocation = self.run_allocation(
                    row.strategy_id, trade_date=effective_date
                )
                if allocation is None:
                    continue

                rebalance_cfg = config.rebalance
                items.append(
                    StarredStrategyItem(
                        strategy_id=row.strategy_id,
                        display_name=row.display_name,
                        frequency=row.frequency,
                        is_rebalance_day=is_rebalance_day,
                        rebalance_frequency=rebalance_cfg.frequency if rebalance_cfg else "daily",
                        rebalance_day_of_week=rebalance_cfg.day_of_week if rebalance_cfg else None,
                        rebalance_day_of_month=rebalance_cfg.day_of_month if rebalance_cfg else None,
                        timing=allocation.timing,
                        rankings=allocation.rankings,
                        plan=allocation.plan,
                        data_date=allocation.data_date,
                    )
                )
            except Exception:
                logger.exception(
                    "获取星标策略 %s 执行摘要失败，跳过", row.strategy_id
                )
                continue

        return StarredSummaryResponse(trade_date=effective_date, items=items)

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
