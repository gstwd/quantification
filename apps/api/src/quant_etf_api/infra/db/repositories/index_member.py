"""指数成分事件仓库（PIT 取样与当前快照读写）。"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from quant_etf_api.infra.db.models.core import IndexMemberEventModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class IndexMemberEventRepository(BaseRepository):
    """index_member_event 表的读写仓库（写操作由调用方提交事务）。"""

    def find_events(
        self,
        index_code: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[IndexMemberEventModel]:
        """查询单指数成分事件（按 start_date 升序）。"""
        query = self._db.query(IndexMemberEventModel).filter(
            IndexMemberEventModel.index_code == index_code
        )
        if start_date is not None:
            query = query.filter(IndexMemberEventModel.start_date >= start_date)
        if end_date is not None:
            query = query.filter(IndexMemberEventModel.start_date <= end_date)
        return query.order_by(
            IndexMemberEventModel.start_date.asc(),
            IndexMemberEventModel.stock_code.asc(),
        ).all()

    def find_members_asof(
        self,
        index_code: str,
        trade_date: date,
    ) -> list[IndexMemberEventModel]:
        """查询 trade_date 当日有效的成分事件行。"""
        return (
            self._db.query(IndexMemberEventModel)
            .filter(
                and_(
                    IndexMemberEventModel.index_code == index_code,
                    IndexMemberEventModel.start_date <= trade_date,
                    IndexMemberEventModel.end_date.is_(None)
                    | (IndexMemberEventModel.end_date > trade_date),
                )
            )
            .all()
        )

    def latest_start_date(self, index_code: str) -> date | None:
        """查询单指数成分事件的最新起始日期。"""
        from sqlalchemy import func

        return (
            self._db.query(func.max(IndexMemberEventModel.start_date))
            .filter(IndexMemberEventModel.index_code == index_code)
            .scalar()
        )

    def replace_current_snapshot(self, index_code: str, rows: list[dict[str, Any]]) -> int:
        """替换单指数全部当前快照行（先删后插，保证幂等刷新）。"""
        deleted = (
            self._db.query(IndexMemberEventModel)
            .filter(
                and_(
                    IndexMemberEventModel.index_code == index_code,
                    IndexMemberEventModel.snapshot_type == "current_snapshot",
                )
            )
            .delete(synchronize_session=False)
        )
        if not rows:
            return int(deleted)
        self._db.bulk_insert_mappings(IndexMemberEventModel, rows)
        return int(deleted) + len(rows)

    def bulk_upsert_pit(self, rows: list[dict[str, Any]]) -> int:
        """幂等写入 PIT 取样行（冲突时保留已有行，不覆盖）。"""
        if not rows:
            return 0
        stmt = pg_insert(IndexMemberEventModel).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["index_code", "stock_code", "start_date"]
        )
        self._db.execute(stmt)
        # ON CONFLICT DO NOTHING 的 rowcount 语义随驱动不稳定，
        # 统一按参与写入的行数返回（幂等场景与输入一致即可）
        return len(rows)
