"""策略配置仓库。"""

from __future__ import annotations

from datetime import datetime

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

    def find_variants(self, batch_id: str) -> list[StrategyConfigModel]:
        """按派生批次查询稳健性变体草稿策略（D-4）。

        Args:
            batch_id: 稳健性验证批次 ID（``strategy_config.source_batch_id``）。

        Returns:
            该批次派生的变体策略列表（按策略 ID 升序）。
        """
        return (
            self._db.query(StrategyConfigModel)
            .filter(
                StrategyConfigModel.source_batch_id == batch_id,
                StrategyConfigModel.is_variant == True,  # noqa: E712
            )
            .order_by(StrategyConfigModel.strategy_id.asc())
            .all()
        )

    def mark_validation_consumed(
        self, strategy_id: str, note: str | None, consumed_at: datetime
    ) -> bool:
        """记录"该策略的验证期数据已被消费"（D-5，仅首次写入）。

        Args:
            strategy_id: 策略标识。
            note: 消费说明（首次消费来源或人工备注）。
            consumed_at: 首次消费时间（UTC aware）。

        Returns:
            是否写入成功（策略不存在返回 False）。
        """
        model = self._db.get(StrategyConfigModel, strategy_id)
        if model is None:
            return False
        if model.validation_consumed_at is None:
            model.validation_consumed_at = consumed_at
            model.validation_consumed_note = note
        elif note:
            # 已经标记过：保留首次时间，仅追加最新说明，避免覆盖历史
            model.validation_consumed_note = f"{model.validation_consumed_note}；{note}"
        return True

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
