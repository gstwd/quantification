"""策略配置服务：管理策略配置的 CRUD 操作。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.infra.db.models.core import StrategyConfigModel
from quant_etf_api.infra.db.repositories.strategy_config import StrategyConfigRepository
from quant_etf_api.schemas.strategy import (
    StrategyConfigCreate,
    StrategyConfigUpdate,
    StrategyDetail,
    StrategySummary,
    StrategyValidationResult,
)

logger = logging.getLogger(__name__)


class StrategyConfigService:
    """策略配置服务，提供配置的增删改查和校验。"""

    def __init__(self, db: Session) -> None:
        """初始化策略配置服务。

        Args:
            db: SQLAlchemy Session。
        """
        self._db = db
        self._repo = StrategyConfigRepository(db)

    def list_configs(self) -> list[StrategySummary]:
        """返回所有启用的策略配置摘要。"""
        rows = self._repo.find_all_active()
        return [
            StrategySummary(
                strategy_id=r.strategy_id,
                display_name=r.display_name,
                version=r.version,
                frequency=r.frequency,
                asset_scope=r.asset_scope,
                description=r.description or "",
                status=r.status,
            )
            for r in rows
        ]

    def get_config(self, strategy_id: str) -> StrategyDetail | None:
        """获取策略配置详情。"""
        row = self._repo.find_by_id(strategy_id)
        if row is None:
            return None
        return StrategyDetail(
            strategy_id=row.strategy_id,
            display_name=row.display_name,
            version=row.version,
            frequency=row.frequency,
            asset_scope=row.asset_scope,
            description=row.description or "",
            status=row.status,
            config_json=row.config_json,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def create_config(self, req: StrategyConfigCreate) -> StrategyDetail:
        """创建策略配置。

        Args:
            req: 创建请求。

        Returns:
            创建后的策略详情。

        Raises:
            ValueError: 策略 ID 已存在。
        """
        existing = self._repo.find_by_id(req.strategy_id)
        if existing is not None:
            raise ValueError(f"策略 {req.strategy_id} 已存在")

        # 校验配置
        validation = self.validate_config(req.config_json)
        if not validation.valid:
            raise ValueError(f"配置校验失败: {'; '.join(validation.errors)}")

        model = StrategyConfigModel(
            strategy_id=req.strategy_id,
            display_name=req.display_name,
            version=req.version,
            description=req.description,
            frequency=req.frequency,
            asset_scope=req.asset_scope,
            config_json=req.config_json,
            status="active",
        )
        self._repo.upsert(model)
        self._db.commit()

        return self.get_config(req.strategy_id)

    def update_config(
        self, strategy_id: str, req: StrategyConfigUpdate
    ) -> StrategyDetail | None:
        """更新策略配置。

        Args:
            strategy_id: 策略标识。
            req: 更新请求。

        Returns:
            更新后的策略详情，不存在返回 None。
        """
        existing = self._repo.find_by_id(strategy_id)
        if existing is None:
            return None

        if req.config_json is not None:
            validation = self.validate_config(req.config_json)
            if not validation.valid:
                raise ValueError(f"配置校验失败: {'; '.join(validation.errors)}")

        if req.display_name is not None:
            existing.display_name = req.display_name
        if req.version is not None:
            existing.version = req.version
        if req.description is not None:
            existing.description = req.description
        if req.frequency is not None:
            existing.frequency = req.frequency
        if req.asset_scope is not None:
            existing.asset_scope = req.asset_scope
        if req.config_json is not None:
            existing.config_json = req.config_json
        if req.status is not None:
            existing.status = req.status

        self._db.commit()
        return self.get_config(strategy_id)

    def delete_config(self, strategy_id: str) -> bool:
        """删除策略配置。

        Args:
            strategy_id: 策略标识。

        Returns:
            是否成功删除。
        """
        result = self._repo.delete_by_id(strategy_id)
        if result:
            self._db.commit()
        return result

    def validate_config(self, config_json: dict[str, Any]) -> StrategyValidationResult:
        """校验策略配置 JSON 是否合法。

        Args:
            config_json: 策略配置 JSON（仅含引擎配置，不含 strategy_id/display_name 等元数据字段）。

        Returns:
            校验结果。
        """
        errors: list[str] = []
        warnings: list[str] = []

        try:
            # strategy_id 和 display_name 存储在顶层列中，校验时补入占位值
            validation_input = {
                "strategy_id": "_validate_",
                "display_name": "_validate_",
                **config_json,
            }
            config = StrategyConfig(**validation_input)
        except Exception as e:
            return StrategyValidationResult(valid=False, errors=[f"配置解析失败: {e}"])

        # 必填字段校验
        if not config.strategy_id:
            errors.append("strategy_id 不能为空")
        if not config.display_name:
            errors.append("display_name 不能为空")
        if not config.score.factors:
            errors.append("score.factors 不能为空")

        # 评分权重校验
        for factor_id, weight in config.score.factors.items():
            if weight == 0:
                warnings.append(f"因子 {factor_id} 权重为 0，将不参与评分")

        # 过滤规则校验
        valid_ops = {"gt", "lt", "gte", "lte", "eq", "neq", "between"}
        if config.filters:
            for rule in config.filters.rules:
                if rule.op not in valid_ops:
                    errors.append(f"过滤规则操作符 '{rule.op}' 不合法，可用: {valid_ops}")
                if rule.op == "between" and not isinstance(rule.value, list):
                    errors.append("between 操作符需要 list 类型的 value")

        # 排名配置校验
        if config.rank.top_n is not None and config.rank.bottom_n is not None:
            warnings.append("top_n 和 bottom_n 同时设置，top_n 优先")

        # 组合配置校验
        if config.portfolio:
            valid_methods = {"equal_weight", "score_weight", "winner_take_all"}
            if config.portfolio.method not in valid_methods:
                errors.append(f"权重分配方法 '{config.portfolio.method}' 不合法，可用: {valid_methods}")

        # 风控配置校验
        if config.risk:
            if config.risk.max_asset_weight <= 0 or config.risk.max_asset_weight > 1:
                errors.append("max_asset_weight 必须在 (0, 1] 范围内")
            if config.risk.min_cash_ratio < 0 or config.risk.min_cash_ratio >= 1:
                errors.append("min_cash_ratio 必须在 [0, 1) 范围内")

        return StrategyValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def get_parsed_config(self, strategy_id: str) -> StrategyConfig | None:
        """获取解析后的 StrategyConfig 对象。

        Args:
            strategy_id: 策略标识。

        Returns:
            解析后的配置对象，不存在返回 None。
        """
        row = self._repo.find_by_id(strategy_id)
        if row is None:
            return None
        try:
            full_config = {
                "strategy_id": row.strategy_id,
                "display_name": row.display_name,
                "version": row.version,
                "description": row.description or "",
                "frequency": row.frequency,
                "asset_scope": row.asset_scope,
                **row.config_json,
            }
            return StrategyConfig(**full_config)
        except Exception:
            logger.exception("解析策略配置 %s 失败", strategy_id)
            return None
