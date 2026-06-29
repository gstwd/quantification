"""关键词→资产标签映射配置仓库。

提供 keyword_tag_config 表的 CRUD 操作和批量查询。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from quant_etf_api.infra.db.models.core import KeywordTagConfigModel
from quant_etf_api.infra.db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class KeywordTagConfigRepository(BaseRepository):
    """关键词→资产标签映射配置仓库。"""

    def find_all_active(self) -> list[KeywordTagConfigModel]:
        """查询所有活跃的映射配置，按优先级降序。

        Returns:
            KeywordTagConfigModel 列表。
        """
        return (
            self._db.query(KeywordTagConfigModel)
            .filter(KeywordTagConfigModel.is_active.is_(True))
            .order_by(KeywordTagConfigModel.priority.desc(), KeywordTagConfigModel.id)
            .all()
        )

    def find_all(
        self,
        offset: int = 0,
        limit: int = 100,
        active_only: bool = False,
    ) -> list[KeywordTagConfigModel]:
        """分页查询所有映射配置。

        Args:
            offset: 偏移量。
            limit: 每页数量。
            active_only: 是否仅返回活跃的配置。

        Returns:
            KeywordTagConfigModel 列表。
        """
        q = self._db.query(KeywordTagConfigModel)
        if active_only:
            q = q.filter(KeywordTagConfigModel.is_active.is_(True))
        return q.order_by(KeywordTagConfigModel.priority.desc(), KeywordTagConfigModel.id).offset(offset).limit(limit).all()

    def count_all(self, active_only: bool = False) -> int:
        """统计映射配置总数。

        Args:
            active_only: 是否仅统计活跃的配置。

        Returns:
            记录总数。
        """
        from sqlalchemy import func

        q = self._db.query(func.count(KeywordTagConfigModel.id))
        if active_only:
            q = q.filter(KeywordTagConfigModel.is_active.is_(True))
        return q.scalar() or 0

    def get_keyword_map(self) -> dict[str, str]:
        """获取当前活跃的关键词→标签映射字典。

        按优先级降序排列，同一关键词的多条记录只有第一条生效
        （unique 约束保证了 keyword 唯一性，此处主要是按优先级排序）。

        Returns:
            keyword → tag 的映射字典。
        """
        rows = self.find_all_active()
        return {r.keyword: r.tag for r in rows}

    def find_by_id(self, config_id: int) -> KeywordTagConfigModel | None:
        """按 ID 查询单条配置。

        Args:
            config_id: 配置 ID。

        Returns:
            KeywordTagConfigModel 或 None。
        """
        return (
            self._db.query(KeywordTagConfigModel)
            .filter(KeywordTagConfigModel.id == config_id)
            .first()
        )

    def create(self, data: dict[str, Any]) -> KeywordTagConfigModel:
        """创建新的关键词映射。

        Args:
            data: 包含 keyword/tag/is_active/priority 的字典。

        Returns:
            新创建的 KeywordTagConfigModel 实例。
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        model = KeywordTagConfigModel(
            keyword=data["keyword"],
            tag=data["tag"],
            is_active=data.get("is_active", True),
            priority=data.get("priority", 0),
            created_at=now,
            updated_at=now,
        )
        self._db.add(model)
        self._db.commit()
        self._db.refresh(model)
        return model

    def update(self, config_id: int, data: dict[str, Any]) -> KeywordTagConfigModel | None:
        """更新关键词映射。

        Args:
            config_id: 配置 ID。
            data: 要更新的字段字典（仅更新提供的字段）。

        Returns:
            更新后的 KeywordTagConfigModel 或 None（ID 不存在时）。
        """
        model = self.find_by_id(config_id)
        if model is None:
            return None

        updatable = ("keyword", "tag", "is_active", "priority")
        for key in updatable:
            if key in data and data[key] is not None:
                setattr(model, key, data[key])

        model.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._db.commit()
        self._db.refresh(model)
        return model

    def delete(self, config_id: int) -> bool:
        """软删除关键词映射（设置 is_active=False）。

        Args:
            config_id: 配置 ID。

        Returns:
            True 表示删除成功，False 表示 ID 不存在。
        """
        model = self.find_by_id(config_id)
        if model is None:
            return False

        model.is_active = False
        model.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._db.commit()
        return True

    def batch_import(
        self,
        mappings: dict[str, str],
    ) -> dict[str, int]:
        """批量导入关键词映射（ON CONFLICT DO UPDATE）。

        Args:
            mappings: keyword → tag 的字典。

        Returns:
            {"created": N, "updated": N} 统计。
        """
        from sqlalchemy.dialects.postgresql import insert

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [
            {
                "keyword": kw,
                "tag": tag,
                "is_active": True,
                "priority": 0,
                "created_at": now,
                "updated_at": now,
            }
            for kw, tag in mappings.items()
        ]

        if not rows:
            return {"created": 0, "updated": 0}

        stmt = insert(KeywordTagConfigModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["keyword"],
            set_={
                "tag": stmt.excluded.tag,
                "updated_at": now,
            },
        )

        try:
            result = self._db.execute(stmt)
            self._db.commit()
            # ON CONFLICT DO UPDATE 的 rowcount 对已更新的行可能不止 1
            return {"created": result.rowcount, "updated": 0}
        except Exception:
            self._db.rollback()
            logger.exception("批量导入关键词标签失败")
            raise
