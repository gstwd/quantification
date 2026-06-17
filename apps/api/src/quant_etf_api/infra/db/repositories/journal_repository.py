"""日志模块数据访问层。"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    JournalAIAnalysisModel,
    JournalEntryModel,
    JournalEntryTagModel,
    JournalIndexSnapshotModel,
    JournalMarketDataModel,
    JournalObservationModel,
    JournalTagModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository


# 预定义的 10 个观察分区
_OBSERVATION_SECTIONS = [
    ("biggest_phenomenon", "今日最大现象", 1),
    ("strongest_direction", "今日最强方向", 2),
    ("weakest_direction", "今日最弱方向", 3),
    ("reason_analysis", "原因分析", 4),
    ("biggest_question", "今日最大疑问", 5),
    ("if_continues_up", "若继续上涨", 6),
    ("if_turns_down", "若转为下跌", 7),
    ("reflection", "今日反思", 8),
    ("experience", "经验沉淀", 9),
    ("watch_next", "后续关注", 10),
]


class JournalRepository(BaseRepository):
    """日志数据访问仓库，封装 journal 模块的所有数据库查询。"""

    # =========================================================================
    # 日志 CRUD
    # =========================================================================

    def create_entry(self, trade_date: date) -> JournalEntryModel:
        """创建一条新的日志记录（仅含日期，其余字段留空）。

        Args:
            trade_date: 交易日期。

        Returns:
            新创建的 JournalEntryModel 实例（已 flush，未 commit）。
        """
        entry = JournalEntryModel(trade_date=trade_date)
        self._db.add(entry)
        self._db.flush()
        return entry

    def find_entry_by_id(self, entry_id: str) -> JournalEntryModel | None:
        """按 ID 查找日志。

        Args:
            entry_id: 日志 UUID。

        Returns:
            找到的模型实例，未找到返回 None。
        """
        return self._db.get(JournalEntryModel, entry_id)

    def find_entry_by_date(self, trade_date: date) -> JournalEntryModel | None:
        """按交易日期查找日志。

        Args:
            trade_date: 交易日期。

        Returns:
            找到的模型实例，未找到返回 None。
        """
        return (
            self._db.query(JournalEntryModel)
            .filter(JournalEntryModel.trade_date == trade_date)
            .first()
        )

    def list_entries(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        tag_id: str | None = None,
        phase: str | None = None,
        is_complete: bool | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[JournalEntryModel], int]:
        """分页查询日志列表，支持多种筛选条件。

        Args:
            date_from: 起始日期（含）。
            date_to: 结束日期（含）。
            tag_id: 按标签过滤。
            phase: 按市场阶段过滤。
            is_complete: 按完成状态过滤。
            offset: 分页偏移。
            limit: 每页条数。

        Returns:
            (日志列表, 总数) 元组。
        """
        q = self._db.query(JournalEntryModel)

        if date_from is not None:
            q = q.filter(JournalEntryModel.trade_date >= date_from)
        if date_to is not None:
            q = q.filter(JournalEntryModel.trade_date <= date_to)
        if phase is not None:
            q = q.filter(JournalEntryModel.market_phase == phase)
        if is_complete is not None:
            q = q.filter(JournalEntryModel.is_complete == is_complete)
        if tag_id is not None:
            q = q.join(
                JournalEntryTagModel,
                JournalEntryModel.id == JournalEntryTagModel.entry_id,
            ).filter(JournalEntryTagModel.tag_id == tag_id)

        total = q.count()
        entries = q.order_by(JournalEntryModel.trade_date.desc()).offset(offset).limit(limit).all()
        return entries, total

    def update_entry(self, entry_id: str, **kwargs: Any) -> JournalEntryModel | None:
        """更新日志的可变字段。

        Args:
            entry_id: 日志 ID。
            **kwargs: 需要更新的字段名和值。

        Returns:
            更新后的模型实例，未找到返回 None。
        """
        entry = self._db.get(JournalEntryModel, entry_id)
        if entry is None:
            return None
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        self._db.flush()
        return entry

    def delete_entry(self, entry_id: str) -> bool:
        """删除日志（CASCADE 自动清理关联数据）。

        Args:
            entry_id: 日志 ID。

        Returns:
            是否成功删除。
        """
        entry = self._db.get(JournalEntryModel, entry_id)
        if entry is None:
            return False
        self._db.delete(entry)
        self._db.flush()
        return True

    # =========================================================================
    # 指数快照
    # =========================================================================

    def bulk_upsert_snapshots(self, entry_id: str, snapshots: list[dict[str, Any]]) -> None:
        """批量 upsert 指数快照（先删后插，保持快照数据最新）。

        Args:
            entry_id: 日志 ID。
            snapshots: 快照字典列表。
        """
        # 先删除该日志的旧快照
        self._db.query(JournalIndexSnapshotModel).filter(
            JournalIndexSnapshotModel.entry_id == entry_id
        ).delete()
        # 批量插入新快照
        for snap in snapshots:
            snap["entry_id"] = entry_id
            row = JournalIndexSnapshotModel(**snap)
            self._db.add(row)
        self._db.flush()

    def find_snapshots_by_entry(self, entry_id: str) -> list[JournalIndexSnapshotModel]:
        """查询某日志的所有指数快照。

        Args:
            entry_id: 日志 ID。

        Returns:
            快照列表（按 sort_order 排序）。
        """
        return (
            self._db.query(JournalIndexSnapshotModel)
            .filter(JournalIndexSnapshotModel.entry_id == entry_id)
            .order_by(JournalIndexSnapshotModel.sort_order)
            .all()
        )

    # =========================================================================
    # 手动市场数据
    # =========================================================================

    def upsert_market_data(self, entry_id: str, data: dict[str, Any]) -> None:
        """更新或插入市场数据（ON CONFLICT upsert）。

        Args:
            entry_id: 日志 ID。
            data: 市场数据字段字典。
        """
        existing = (
            self._db.query(JournalMarketDataModel)
            .filter(JournalMarketDataModel.entry_id == entry_id)
            .first()
        )
        if existing is not None:
            for key, value in data.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
        else:
            row = JournalMarketDataModel(entry_id=entry_id, **data)
            self._db.add(row)
        self._db.flush()

    def find_market_data_by_entry(self, entry_id: str) -> JournalMarketDataModel | None:
        """查询某日志的手动市场数据。

        Args:
            entry_id: 日志 ID。

        Returns:
            市场数据模型实例，未填写返回 None。
        """
        return (
            self._db.query(JournalMarketDataModel)
            .filter(JournalMarketDataModel.entry_id == entry_id)
            .first()
        )

    # =========================================================================
    # 观察分区
    # =========================================================================

    def create_empty_observation_sections(self, entry_id: str) -> None:
        """为新日志预创建 10 个空观察分区。

        Args:
            entry_id: 日志 ID。
        """
        for section_key, section_label, sort_order in _OBSERVATION_SECTIONS:
            row = JournalObservationModel(
                entry_id=entry_id,
                section_key=section_key,
                section_label=section_label,
                sort_order=sort_order,
            )
            self._db.add(row)
        self._db.flush()

    def bulk_upsert_observations(self, entry_id: str, observations: list[dict[str, Any]]) -> None:
        """批量 upsert 观察分区内容。

        Args:
            entry_id: 日志 ID。
            observations: [{"section_key": ..., "content": ...}, ...] 列表。
        """
        for obs in observations:
            existing = (
                self._db.query(JournalObservationModel)
                .filter(
                    JournalObservationModel.entry_id == entry_id,
                    JournalObservationModel.section_key == obs["section_key"],
                )
                .first()
            )
            if existing is not None:
                existing.content = obs.get("content")
            else:
                # 如果分区不存在（不应发生），创建新记录
                row = JournalObservationModel(
                    entry_id=entry_id,
                    section_key=obs["section_key"],
                    section_label=obs.get("section_label", obs["section_key"]),
                    content=obs.get("content"),
                )
                self._db.add(row)
        self._db.flush()

    def find_observations_by_entry(self, entry_id: str) -> list[JournalObservationModel]:
        """查询某日志的所有观察分区。

        Args:
            entry_id: 日志 ID。

        Returns:
            观察分区列表（按 sort_order 排序）。
        """
        return (
            self._db.query(JournalObservationModel)
            .filter(JournalObservationModel.entry_id == entry_id)
            .order_by(JournalObservationModel.sort_order)
            .all()
        )

    def count_observation_words(self, entry_id: str) -> int:
        """统计某日志所有观察分区的总字数。

        Args:
            entry_id: 日志 ID。

        Returns:
            总字数。
        """
        rows = (
            self._db.query(JournalObservationModel.content)
            .filter(
                JournalObservationModel.entry_id == entry_id,
                JournalObservationModel.content.isnot(None),
            )
            .all()
        )
        return sum(len(row.content or "") for row in rows)

    # =========================================================================
    # 标签
    # =========================================================================

    def find_all_tags(self) -> list[JournalTagModel]:
        """查询所有标签（按使用次数降序）。

        Returns:
            标签列表。
        """
        return (
            self._db.query(JournalTagModel)
            .order_by(JournalTagModel.usage_count.desc(), JournalTagModel.name)
            .all()
        )

    def find_tag_by_id(self, tag_id: str) -> JournalTagModel | None:
        """按 ID 查找标签。

        Args:
            tag_id: 标签 ID。

        Returns:
            标签模型实例，未找到返回 None。
        """
        return self._db.get(JournalTagModel, tag_id)

    def create_tag(self, name: str, color: str = "#3B82F6", description: str | None = None) -> JournalTagModel:
        """创建标签。

        Args:
            name: 标签名称。
            color: 颜色值。
            description: 标签说明。

        Returns:
            新创建的标签模型实例。
        """
        tag = JournalTagModel(name=name, color=color, description=description)
        self._db.add(tag)
        self._db.flush()
        return tag

    def update_tag(self, tag_id: str, **kwargs: Any) -> JournalTagModel | None:
        """更新标签字段。

        Args:
            tag_id: 标签 ID。
            **kwargs: 需要更新的字段。

        Returns:
            更新后的标签，未找到返回 None。
        """
        tag = self._db.get(JournalTagModel, tag_id)
        if tag is None:
            return None
        for key, value in kwargs.items():
            if hasattr(tag, key):
                setattr(tag, key, value)
        self._db.flush()
        return tag

    def delete_tag(self, tag_id: str) -> bool:
        """删除标签（仅非系统标签可删除）。

        Args:
            tag_id: 标签 ID。

        Returns:
            是否成功删除。
        """
        tag = self._db.get(JournalTagModel, tag_id)
        if tag is None or tag.is_system:
            return False
        self._db.delete(tag)
        self._db.flush()
        return True

    def find_tags_for_entry(self, entry_id: str) -> list[JournalTagModel]:
        """查询某日志关联的所有标签。

        Args:
            entry_id: 日志 ID。

        Returns:
            标签列表。
        """
        return (
            self._db.query(JournalTagModel)
            .join(JournalEntryTagModel, JournalTagModel.id == JournalEntryTagModel.tag_id)
            .filter(JournalEntryTagModel.entry_id == entry_id)
            .all()
        )

    def set_entry_tags(self, entry_id: str, tag_ids: list[str]) -> None:
        """全量替换日志的标签（先删后插），并更新 usage_count。

        Args:
            entry_id: 日志 ID。
            tag_ids: 新的标签 ID 列表。
        """
        # 删除已有映射
        self._db.query(JournalEntryTagModel).filter(
            JournalEntryTagModel.entry_id == entry_id
        ).delete()
        # 插入新映射
        for tag_id in tag_ids:
            mapping = JournalEntryTagModel(entry_id=entry_id, tag_id=tag_id)
            self._db.add(mapping)
        # 更新 usage_count
        self._recalculate_tag_usage_counts()
        self._db.flush()

    def _recalculate_tag_usage_counts(self) -> None:
        """重新计算所有标签的使用次数。"""
        from sqlalchemy import func

        counts = (
            self._db.query(
                JournalEntryTagModel.tag_id,
                func.count(JournalEntryTagModel.entry_id).label("cnt"),
            )
            .group_by(JournalEntryTagModel.tag_id)
            .all()
        )
        count_map = {tag_id: cnt for tag_id, cnt in counts}
        all_tags = self._db.query(JournalTagModel).all()
        for tag in all_tags:
            tag.usage_count = count_map.get(tag.id, 0)

    # =========================================================================
    # AI 分析
    # =========================================================================

    def upsert_ai_analysis(self, entry_id: str, **kwargs: Any) -> None:
        """更新或插入 AI 分析结果。

        Args:
            entry_id: 日志 ID。
            **kwargs: AI 分析字段（model, status, market_summary 等）。
        """
        existing = (
            self._db.query(JournalAIAnalysisModel)
            .filter(JournalAIAnalysisModel.entry_id == entry_id)
            .first()
        )
        if existing is not None:
            for key, value in kwargs.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
        else:
            row = JournalAIAnalysisModel(entry_id=entry_id, **kwargs)
            self._db.add(row)
        self._db.flush()

    def find_ai_analysis_by_entry(self, entry_id: str) -> JournalAIAnalysisModel | None:
        """查询某日志的 AI 分析结果。

        Args:
            entry_id: 日志 ID。

        Returns:
            AI 分析模型实例，不存在返回 None。
        """
        return (
            self._db.query(JournalAIAnalysisModel)
            .filter(JournalAIAnalysisModel.entry_id == entry_id)
            .first()
        )
