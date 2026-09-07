"""因子元数据治理服务。

负责因子目录（代码侧）与 factor_definition（DB 侧）的幂等同步，
与因子计算编排（FactorService）分离 —— 两者生命周期不同：
元数据同步在部署/升级时执行，因子计算在每日调度与补算时执行。

因子目录 = 已注册进 FactorRegistry 的全部因子（FactorSpec）。行业面板
（申万 RRG/扩散）只作为指数级因子的内部数据依赖，由 domain/industry
纯算法与 IndustryFactorService 维护，不再登记为可配置的正式因子。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from quant_etf_api.factors.registry import FactorRegistry
from quant_etf_api.infra.db.models.core import FactorDefinitionModel
from quant_etf_api.infra.db.repositories.factor_definition import FactorDefinitionRepository

logger = logging.getLogger(__name__)


class FactorAdminService:
    """因子元数据治理服务（定义同步、状态查询）。"""

    def __init__(self, db: Session, registry: FactorRegistry) -> None:
        """初始化因子元数据治理服务。

        Args:
            db: SQLAlchemy 同步 Session。
            registry: 因子注册表（代码侧元数据来源）。
        """
        self._db = db
        self._registry = registry
        self._repo = FactorDefinitionRepository(db)

    def sync_factor_definitions(self) -> dict[str, int]:
        """将注册表中的 FactorSpec 同步到 factor_definition 表（幂等）。

        同步策略：
        - 代码中有、DB 中没有 → INSERT（新因子）
        - 代码和 DB 都有 → 仅更新代码管控字段（version/required_data/
          四轴元数据 asset_domain/value_shape/usage/default_params）
        - DB 中有、代码中没有 → 设为 is_active=False（保留历史数据关联）

        Returns:
            同步统计字典：new / updated / deactivated。
        """
        specs = {s.factor_id: s for s in self._registry.specs()}
        existing = {d.factor_id: d for d in self._repo.find_all()}

        new_count = 0
        update_count = 0
        deactivate_count = 0

        for factor_id, spec in specs.items():
            if factor_id not in existing:
                self._db.add(
                    FactorDefinitionModel(
                        factor_id=spec.factor_id,
                        name=spec.name,
                        category=spec.category,
                        version=spec.version,
                        description=spec.description,
                        required_data=spec.required_data,
                        asset_domain=spec.asset_domain,
                        value_shape=spec.value_shape,
                        usage=list(spec.usage),
                        default_params=dict(spec.default_params),
                        owner_plugin=None,
                        is_active=True,
                    )
                )
                new_count += 1
            else:
                row = existing[factor_id]
                changed = False
                if row.version != spec.version:
                    row.version = spec.version
                    changed = True
                if row.required_data != spec.required_data:
                    row.required_data = spec.required_data
                    changed = True
                # 四轴元数据为代码管控字段：代码演进后同步覆盖 DB 值，
                # 保证因子中心展示与引擎/回测消费口径一致
                if row.asset_domain != spec.asset_domain:
                    row.asset_domain = spec.asset_domain
                    changed = True
                if row.value_shape != spec.value_shape:
                    row.value_shape = spec.value_shape
                    changed = True
                if (row.usage or []) != list(spec.usage):
                    row.usage = list(spec.usage)
                    changed = True
                if (row.default_params or {}) != dict(spec.default_params):
                    row.default_params = dict(spec.default_params)
                    changed = True
                if changed:
                    update_count += 1

        for factor_id, row in existing.items():
            if factor_id not in specs and row.is_active:
                row.is_active = False
                deactivate_count += 1

        if new_count or update_count or deactivate_count:
            try:
                self._db.commit()
                logger.info(
                    "因子定义同步完成: 新增=%d 更新=%d 停用=%d",
                    new_count,
                    update_count,
                    deactivate_count,
                )
            except Exception:
                self._db.rollback()
                logger.warning("因子定义同步失败", exc_info=True)
                raise

        return {"new": new_count, "updated": update_count, "deactivated": deactivate_count}
