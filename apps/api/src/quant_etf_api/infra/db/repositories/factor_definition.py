"""因子定义仓库，提供因子元数据的查询方法。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import FactorDefinitionModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class FactorDefinitionRepository(BaseRepository):
    """FactorDefinitionModel 的查询仓库。"""

    def __init__(self, db: Session) -> None:
        """初始化因子定义仓库。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        super().__init__(db)

    def find_all(self) -> list[FactorDefinitionModel]:
        """返回所有因子定义（含已禁用），按 factor_id 排序。"""
        return self._db.query(FactorDefinitionModel).order_by(FactorDefinitionModel.factor_id).all()

    def find_active(self) -> list[FactorDefinitionModel]:
        """返回所有启用的因子定义，供计算使用。"""
        return (
            self._db.query(FactorDefinitionModel)
            .filter(FactorDefinitionModel.is_active.is_(True))
            .order_by(FactorDefinitionModel.factor_id)
            .all()
        )

    def find_by_id(self, factor_id: str) -> FactorDefinitionModel | None:
        """按主键查询单条因子定义。"""
        return self._db.get(FactorDefinitionModel, factor_id)
