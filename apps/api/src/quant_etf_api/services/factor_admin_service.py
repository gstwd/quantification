"""因子元数据治理服务。

负责因子模板目录（代码侧）与 factor_definition（DB 侧）的幂等同步，
与因子现算编排（FactorComputeService）分离 —— 两者生命周期不同：
元数据同步在部署/升级时执行，因子计算在每次实时分配、因子查询或研究任务中执行。

因子目录 = 已注册进 FactorTemplateRegistry 的全部模板。行业面板
（申万 RRG/扩散）只作为指数级因子的内部数据依赖，由 domain/industry
纯算法与 IndustryFactorService 维护，不再登记为可配置的正式因子。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from quant_etf_api.factors.catalog import FactorTemplateRegistry
from quant_etf_api.infra.db.models.core import FactorDefinitionModel
from quant_etf_api.infra.db.repositories.factor_definition import FactorDefinitionRepository

logger = logging.getLogger(__name__)


class FactorAdminService:
    """因子元数据治理服务（定义同步、状态查询）。"""

    def __init__(self, db: Session, registry: FactorTemplateRegistry) -> None:
        """初始化因子元数据治理服务。

        Args:
            db: SQLAlchemy 同步 Session。
            registry: 因子模板注册表（代码侧元数据来源）。
        """
        self._db = db
        self._registry = registry
        self._repo = FactorDefinitionRepository(db)

    def sync_factor_definitions(self) -> dict[str, int]:
        """将注册表中的模板元数据同步到 factor_definition 表（幂等）。

        同步策略：
        - 代码中有、DB 中没有 → INSERT（新模板）
        - 代码和 DB 都有 → 仅更新代码管控字段（version/required_data/
          value_shape/usage/parameter_schema/default_params）
        - DB 中有、代码中没有 → 设为 is_active=False

        Returns:
            同步统计字典：new / updated / deactivated。
        """
        templates = {template.template_id: template for template in self._registry.all()}
        existing = {d.factor_id: d for d in self._repo.find_all()}

        new_count = 0
        update_count = 0
        deactivate_count = 0

        for template_id, template in templates.items():
            spec = template.spec()
            managed = {
                "version": spec.version,
                "required_data": list(spec.required_data),
                "value_shape": spec.value_shape,
                "usage": list(spec.usage),
                "parameter_schema": {
                    name: parameter.to_payload()
                    for name, parameter in template.parameter_schema.items()
                },
                "default_params": dict(template.default_params),
            }
            if template_id not in existing:
                self._db.add(
                    FactorDefinitionModel(
                        factor_id=template_id,
                        name=spec.name,
                        category=spec.category,
                        description=spec.description,
                        is_active=True,
                        **managed,
                    )
                )
                new_count += 1
                continue

            row = existing[template_id]
            changed = False
            for field, value in managed.items():
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changed = True
            if changed:
                update_count += 1

        for factor_id, row in existing.items():
            if factor_id not in templates and row.is_active:
                row.is_active = False
                deactivate_count += 1

        if new_count or update_count or deactivate_count:
            try:
                self._db.commit()
                logger.info(
                    "因子模板同步完成: 新增=%d 更新=%d 停用=%d",
                    new_count,
                    update_count,
                    deactivate_count,
                )
            except Exception:
                self._db.rollback()
                logger.warning("因子模板同步失败", exc_info=True)
                raise

        return {"new": new_count, "updated": update_count, "deactivated": deactivate_count}
