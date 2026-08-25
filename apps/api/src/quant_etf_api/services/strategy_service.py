"""策略服务（薄门面）：列表、详情、配置管理和星标管理。

决策管线（run_allocation / get_starred_summary）已收敛到
StrategyDecisionService（统一执行入口，对应 C1），本类仅保留
配置 CRUD、星标等轻量委托，避免职责混杂（对应 7.4#2）。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.schemas.strategy import (
    AllocationResponse,
    StarredSummaryResponse,
    StrategyConfigCreate,
    StrategyConfigUpdate,
    StrategyDetail,
    StrategySummary,
    StrategyValidationResult,
)
from quant_etf_api.services.strategy_config_service import StrategyConfigService
from quant_etf_api.services.strategy_decision_service import StrategyDecisionService

logger = logging.getLogger(__name__)


class StrategyService:
    """策略服务，提供策略列表、详情、配置管理和星标管理。"""

    def __init__(self, db: Session | None = None) -> None:
        """初始化策略服务。

        Args:
            db: SQLAlchemy Session，配置管理和决策管线需要。
        """
        self._db = db

    def _config_svc(self) -> StrategyConfigService:
        """惰性创建配置服务。

        Returns:
            配置服务实例。

        Raises:
            ValueError: 未提供数据库 Session。
        """
        if self._db is None:
            raise ValueError("未提供数据库 Session")
        return StrategyConfigService(self._db)

    def _decision_svc(self) -> StrategyDecisionService:
        """惰性创建统一决策服务。

        Returns:
            统一决策服务实例。

        Raises:
            ValueError: 未提供数据库 Session。
        """
        if self._db is None:
            raise ValueError("未提供数据库 Session")
        return StrategyDecisionService(self._db)

    def list_strategies(self) -> list[StrategySummary]:
        """返回所有已启用策略的摘要列表。"""
        if self._db is None:
            return []
        return self._config_svc().list_configs()

    def get_strategy(self, strategy_id: str) -> StrategyDetail | None:
        """按 ID 获取策略详情。"""
        if self._db is None:
            return None
        return self._config_svc().get_config(strategy_id)

    def run_allocation(
        self,
        strategy_id: str,
        params: dict[str, Any] | None = None,
        trade_date: date | None = None,
    ) -> AllocationResponse | None:
        """运行资产配置决策管线（委托统一决策服务）。

        Args:
            strategy_id: 策略标识。
            params: 策略参数覆盖（预留扩展，未使用）。
            trade_date: 指定交易日，不传则使用今天。

        Returns:
            AllocationResponse，策略不存在时返回 None。

        Raises:
            ValueError: 配置校验失败时抛出（路由层转 422）。
        """
        if self._db is None:
            logger.warning("run_allocation: 未提供数据库 Session")
            return None
        return self._decision_svc().run_allocation(strategy_id, trade_date=trade_date)

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
        result = self._config_svc()._repo.set_starred(strategy_id, is_starred)
        if result:
            self._db.commit()
        return result

    def get_starred_summary(self, trade_date: date | None = None) -> StarredSummaryResponse:
        """获取所有星标策略的当日执行摘要（委托统一决策服务）。

        Args:
            trade_date: 指定交易日，不传则使用今天。

        Returns:
            StarredSummaryResponse。
        """
        if self._db is None:
            return StarredSummaryResponse(trade_date=trade_date or date.today(), items=[])
        return self._decision_svc().get_starred_summary(trade_date=trade_date)

    # ── 配置管理委托 ──────────────────────────────────────────────────────

    def create_config(self, req: StrategyConfigCreate) -> StrategyDetail:
        """创建策略配置。"""
        return self._config_svc().create_config(req)

    def update_config(self, strategy_id: str, req: StrategyConfigUpdate) -> StrategyDetail | None:
        """更新策略配置。"""
        if self._db is None:
            return None
        return self._config_svc().update_config(strategy_id, req)

    def delete_config(self, strategy_id: str) -> bool:
        """删除策略配置。"""
        if self._db is None:
            return False
        return self._config_svc().delete_config(strategy_id)

    def validate_config(self, config_json: dict[str, Any]) -> StrategyValidationResult:
        """校验策略配置（含因子 ID 与变换函数校验）。

        Args:
            config_json: 策略配置 JSON。

        Returns:
            校验结果。
        """
        if self._db is None:
            return StrategyValidationResult(
                valid=False,
                errors=["未提供数据库 Session，无法完成因子校验"],
            )
        return self._config_svc().validate_config(config_json)
