"""策略配置服务：管理策略配置的 CRUD 操作。"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.engine.config import SUPPORTED_SCHEMA_VERSIONS, StrategyConfig
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.engine.transforms import list_transform_names
from quant_etf_api.factors.registry import get_default_factor_registry
from quant_etf_api.infra.db.models.core import StrategyConfigModel
from quant_etf_api.infra.db.repositories.factor_definition import FactorDefinitionRepository
from quant_etf_api.infra.db.repositories.strategy_config import StrategyConfigRepository
from quant_etf_api.schemas.strategy import (
    StrategyConfigCreate,
    StrategyConfigUpdate,
    StrategyDetail,
    StrategySummary,
    StrategyValidationResult,
)

logger = logging.getLogger(__name__)


def compute_config_hash(config_json: dict[str, Any]) -> str:
    """计算策略配置的规范化 sha256 哈希。

    使用 sort_keys 与固定分隔符保证同一配置在不同环境下哈希一致，
    用于回测快照与优化会话的前后版本比对。

    Args:
        config_json: 策略配置 JSON（仅引擎配置）。

    Returns:
        64 位十六进制 sha256 哈希。
    """
    canonical = json.dumps(
        config_json,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
                description=r.description or "",
                status=r.status,
                is_starred=r.is_starred,
                index_codes=(r.config_json or {}).get("index_codes", []),
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
            description=row.description or "",
            status=row.status,
            is_starred=row.is_starred,
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

        # 归一化：config_json 未显式声明 schema_version 时写入默认 v1
        config_json = dict(req.config_json)
        config_json.setdefault("schema_version", "1")

        model = StrategyConfigModel(
            strategy_id=req.strategy_id,
            display_name=req.display_name,
            version=req.version,
            description=req.description,
            frequency=req.frequency,
            config_json=config_json,
            status=req.status,
        )
        self._repo.upsert(model)
        self._db.commit()

        return self.get_config(req.strategy_id)

    def update_config(self, strategy_id: str, req: StrategyConfigUpdate) -> StrategyDetail | None:
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
            # 归一化：保留已显式声明的 schema_version，缺失时写入默认 v1
            req.config_json = dict(req.config_json)
            req.config_json.setdefault("schema_version", "1")

        if req.display_name is not None:
            existing.display_name = req.display_name
        if req.version is not None:
            existing.version = req.version
        if req.description is not None:
            existing.description = req.description
        if req.frequency is not None:
            existing.frequency = req.frequency
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
        """校验策略配置 JSON 是否合法（含因子 ID 与变换函数校验）。

        Args:
            config_json: 策略配置 JSON（仅含引擎配置，不含 strategy_id/display_name 等元数据字段）。

        Returns:
            校验结果。
        """
        try:
            # strategy_id 和 display_name 存储在顶层列中，校验时补入占位值
            validation_input = {
                "strategy_id": "_validate_",
                "display_name": "_validate_",
                # 旧配置无 schema_version 字段时按 v1 校验（默认兼容）
                "schema_version": config_json.get("schema_version", "1"),
                **config_json,
            }
            config = StrategyConfig(**validation_input)
        except Exception as e:
            return StrategyValidationResult(valid=False, errors=[f"配置解析失败: {e}"])

        return self.validate_parsed(config)

    def validate_parsed(self, config: StrategyConfig) -> StrategyValidationResult:
        """校验已解析的 StrategyConfig（供运行时路径复用，避免二次解析）。

        在结构校验基础上，额外校验：
        - 引用的每个因子 ID 均存在于注册表且已在 factor_definition 中启用
        - transforms 引用的变换函数均存在于引擎变换注册表

        Args:
            config: 已解析的策略配置。

        Returns:
            校验结果。
        """
        errors, warnings = self._structural_validation(config)
        errors.extend(self._factor_reference_errors(config))
        errors.extend(self._transform_validation_errors(config))
        return StrategyValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def _structural_validation(config: StrategyConfig) -> tuple[list[str], list[str]]:
        """结构字段校验（不依赖数据库，纯静态）。

        Args:
            config: 已解析的策略配置。

        Returns:
            (errors, warnings) 二元组。
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 必填字段校验
        if not config.strategy_id:
            errors.append("strategy_id 不能为空")
        if not config.display_name:
            errors.append("display_name 不能为空")
        if not config.score.factors:
            errors.append("score.factors 不能为空")
        if config.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            errors.append(
                f"不支持的配置 schema_version '{config.schema_version}'，"
                f"可用: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )

        # 择时代理指数校验
        if config.timing and not config.timing.proxy_index_codes:
            errors.append("timing.proxy_index_codes 不能为空")

        # 评分权重校验
        for factor_id, weight in config.score.factors.items():
            if weight == 0:
                warnings.append(f"因子 {factor_id} 权重为 0，将不参与评分")

        # 过滤规则校验
        valid_ops = {"gt", "lt", "gte", "lte", "eq", "neq", "between"}
        valid_missing_strategies = {"pass", "fail", "exclude"}
        if config.filters:
            for rule in config.filters.rules:
                if rule.op not in valid_ops:
                    errors.append(f"过滤规则操作符 '{rule.op}' 不合法，可用: {valid_ops}")
                if rule.missing_strategy not in valid_missing_strategies:
                    errors.append(
                        f"过滤规则 missing_strategy '{rule.missing_strategy}' 不合法，"
                        f"可用: {sorted(valid_missing_strategies)}"
                    )
                # 跨因子比较校验：compare_to 与 value 二选一
                has_value = rule.value is not None
                has_compare = bool(rule.compare_to)
                if has_value and has_compare:
                    errors.append("过滤规则不能同时设置 value 和 compare_to，只能二选一")
                if not has_value and not has_compare:
                    errors.append("过滤规则必须设置 value 或 compare_to 其中之一")
                if has_compare:
                    if rule.op == "between":
                        errors.append("between 操作符不支持跨因子比较（compare_to）")
                else:
                    if rule.op == "between" and not isinstance(rule.value, list):
                        errors.append("between 操作符需要 list 类型的 value")

        # 排名配置校验
        if config.rank.top_n is not None and config.rank.bottom_n is not None:
            warnings.append("top_n 和 bottom_n 同时设置，top_n 优先")

        # 组合配置校验
        if config.portfolio:
            valid_methods = {"equal_weight", "score_weight", "winner_take_all"}
            if config.portfolio.method not in valid_methods:
                errors.append(
                    f"权重分配方法 '{config.portfolio.method}' 不合法，可用: {valid_methods}"
                )

        # 风控配置校验
        if config.risk:
            if config.risk.max_asset_weight <= 0 or config.risk.max_asset_weight > 1:
                errors.append("max_asset_weight 必须在 (0, 1] 范围内")
            if config.risk.min_cash_ratio < 0 or config.risk.min_cash_ratio >= 1:
                errors.append("min_cash_ratio 必须在 [0, 1) 范围内")

        return errors, warnings

    def _factor_reference_errors(self, config: StrategyConfig) -> list[str]:
        """校验配置引用的全部因子 ID 存在且启用。

        合法因子集合 = 注册表存在（可计算）且 factor_definition 中 is_active=True
        （compute_and_store 只计算已启用因子，停用因子永远无值，引用即为静默失效）。

        Args:
            config: 已解析的策略配置。

        Returns:
            错误信息列表。
        """
        required_ids = FactorProvider.collect_required_factor_ids(config)
        if not required_ids:
            return []

        # 因子定义属于 factor_definition 表，必须使用 FactorDefinitionRepository
        # 查询启用集合（StrategyConfigRepository 不负责因子元数据）
        active_ids = {row.factor_id for row in FactorDefinitionRepository(self._db).find_active()}
        registry_ids = {spec.factor_id for spec in get_default_factor_registry().specs()}

        errors: list[str] = []
        for factor_id in required_ids:
            if factor_id not in registry_ids:
                errors.append(f"未知因子 '{factor_id}'：请检查拼写（可用因子见 GET /factors）")
            elif factor_id not in active_ids:
                errors.append(
                    f"因子 '{factor_id}' 已停用或未同步："
                    "请运行 POST /factors/init 同步并确保 is_active=true"
                )
        return errors

    @staticmethod
    def _transform_validation_errors(config: StrategyConfig) -> list[str]:
        """校验 transforms 引用的变换函数均存在。

        覆盖评分、择时及 regime 条件化配置中的 transforms，
        避免未知变换名在引擎执行时才抛出 KeyError。

        Args:
            config: 已解析的策略配置。

        Returns:
            错误信息列表。
        """
        transforms: dict[str, str] = {}
        if config.score:
            transforms.update(config.score.transforms)
        if config.timing:
            transforms.update(config.timing.transforms)
        for regime_rule in config.regime_rules.values():
            if regime_rule.score:
                transforms.update(regime_rule.score.transforms)

        errors: list[str] = []
        for factor_id, transform_name in transforms.items():
            if transform_name and transform_name not in list_transform_names():
                errors.append(
                    f"未知变换函数 '{transform_name}'（用于因子 {factor_id}），"
                    f"可用: {list_transform_names()}"
                )
        return errors

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
                **row.config_json,
            }
            return StrategyConfig(**full_config)
        except Exception:
            logger.exception("解析策略配置 %s 失败", strategy_id)
            return None

    @staticmethod
    def parse_snapshot(snapshot: dict[str, Any]) -> StrategyConfig | None:
        """从回测配置快照重建 StrategyConfig 对象。

        快照包含 strategy_id/display_name/version/frequency 等元数据与
        config_json；解析失败（如快照损坏）时返回 None，由调用方回退到实时配置。

        Args:
            snapshot: 回测快照字典。

        Returns:
            解析后的配置对象，失败返回 None。
        """
        try:
            config_json = snapshot.get("config_json") or {}
            full_config = {
                "strategy_id": snapshot.get("strategy_id", "_snapshot_"),
                "display_name": snapshot.get("display_name", ""),
                "version": snapshot.get("version", "1.0.0"),
                "description": snapshot.get("description", ""),
                "frequency": snapshot.get("frequency", "daily"),
                **config_json,
            }
            return StrategyConfig(**full_config)
        except Exception:
            logger.exception("解析回测配置快照失败")
            return None
