"""策略优化会话仓库。"""

from __future__ import annotations

from typing import Any

from quant_etf_api.infra.db.models.core import StrategyOptimizationModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class OptimizationRepository(BaseRepository):
    """strategy_optimization 表的查询与持久化仓库。"""

    def find_by_id(self, optimization_id: str) -> StrategyOptimizationModel | None:
        """按主键查询优化会话。

        Args:
            optimization_id: 优化会话 ID。

        Returns:
            会话记录，不存在返回 None。
        """
        return self._db.get(StrategyOptimizationModel, optimization_id)

    def create(self, model: StrategyOptimizationModel) -> None:
        """新增优化会话并提交。

        Args:
            model: 会话 ORM 行。
        """
        self._db.add(model)
        self._db.commit()

    def update(self, optimization_id: str, **fields: Any) -> bool:
        """按主键更新会话字段并提交。

        Args:
            optimization_id: 优化会话 ID。
            **fields: 需要更新的字段名与值。

        Returns:
            是否找到并更新。
        """
        model = self.find_by_id(optimization_id)
        if model is None:
            return False
        for key, value in fields.items():
            setattr(model, key, value)
        self._db.commit()
        return True

    def find_all(
        self,
        strategy_id: str | None = None,
        limit: int = 50,
    ) -> list[StrategyOptimizationModel]:
        """分页查询优化会话，按创建时间倒序。

        Args:
            strategy_id: 可选的基线策略 ID 过滤。
            limit: 最大返回条数。

        Returns:
            会话记录列表。
        """
        query = self._db.query(StrategyOptimizationModel)
        if strategy_id:
            query = query.filter(StrategyOptimizationModel.strategy_id == strategy_id)
        return (
            query.order_by(StrategyOptimizationModel.created_at.desc())
            .limit(limit)
            .all()
        )
