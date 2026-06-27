"""策略配置仓库。"""

from __future__ import annotations

from quant_etf_api.infra.db.models.core import StrategyConfigModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class StrategyConfigRepository(BaseRepository):
    """策略配置表的查询与持久化仓库。"""

    def find_all_active(self) -> list[StrategyConfigModel]:
        """查询所有启用的策略配置。"""
        return (
            self._db.query(StrategyConfigModel)
            .filter(StrategyConfigModel.status == "active")
            .order_by(StrategyConfigModel.strategy_id.asc())
            .all()
        )

    def find_by_id(self, strategy_id: str) -> StrategyConfigModel | None:
        """按主键查询策略配置。"""
        return self._db.get(StrategyConfigModel, strategy_id)

    def upsert(self, model: StrategyConfigModel) -> None:
        """插入或更新策略配置。"""
        existing = self._db.get(StrategyConfigModel, model.strategy_id)
        if existing:
            existing.display_name = model.display_name
            existing.version = model.version
            existing.description = model.description
            existing.frequency = model.frequency
            existing.config_json = model.config_json
            existing.status = model.status
        else:
            self._db.add(model)

    def find_starred(self) -> list[StrategyConfigModel]:
        """查询所有星标关注的启用策略。"""
        return (
            self._db.query(StrategyConfigModel)
            .filter(
                StrategyConfigModel.status == "active",
                StrategyConfigModel.is_starred == True,  # noqa: E712
            )
            .order_by(StrategyConfigModel.strategy_id.asc())
            .all()
        )

    def set_starred(self, strategy_id: str, is_starred: bool) -> bool:
        """设置策略的星标状态。

        Args:
            strategy_id: 策略标识。
            is_starred: 是否星标。

        Returns:
            是否成功更新。
        """
        model = self._db.get(StrategyConfigModel, strategy_id)
        if model is None:
            return False
        model.is_starred = is_starred
        return True

    def delete_by_id(self, strategy_id: str) -> bool:
        """删除策略配置。

        Args:
            strategy_id: 策略标识。

        Returns:
            是否成功删除。
        """
        model = self._db.get(StrategyConfigModel, strategy_id)
        if model is None:
            return False
        self._db.delete(model)
        return True
