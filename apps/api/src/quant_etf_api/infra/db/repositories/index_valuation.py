"""指数估值数据仓库。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func

from quant_etf_api.infra.db.models.core import IndexValuationModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class IndexValuationRepository(BaseRepository):
    """IndexValuationModel 的只读查询仓库。"""

    def find_range(
        self,
        start: date,
        end: date,
        index_codes: list[str] | None = None,
    ) -> dict[tuple[str, date], IndexValuationModel]:
        """按日期范围查询指数估值，返回 (code, date) → 行 的映射。

        Args:
            start: 起始日期（含）。
            end: 截止日期（含）。
            index_codes: 指数代码列表，None 时查询全部。

        Returns:
            (index_code, trade_date) → 估值行的映射。
        """
        query = self._db.query(IndexValuationModel).filter(
            and_(
                IndexValuationModel.trade_date >= start,
                IndexValuationModel.trade_date <= end,
            )
        )
        if index_codes:
            query = query.filter(IndexValuationModel.index_code.in_(index_codes))
        rows = query.all()
        return {(r.index_code, r.trade_date): r for r in rows}

    def find_by_code_limit(self, code: str, limit: int) -> list[IndexValuationModel]:
        """按指数代码查询最近 N 条估值记录（升序）。"""
        rows = (
            self._db.query(IndexValuationModel)
            .filter(IndexValuationModel.index_code == code)
            .order_by(IndexValuationModel.trade_date.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

    def find_by_code_date_range(
        self, code: str, start: date, end: date
    ) -> list[IndexValuationModel]:
        """按指数代码和日期范围查询估值（升序）。"""
        return (
            self._db.query(IndexValuationModel)
            .filter(
                and_(
                    IndexValuationModel.index_code == code,
                    IndexValuationModel.trade_date >= start,
                    IndexValuationModel.trade_date <= end,
                )
            )
            .order_by(IndexValuationModel.trade_date.asc())
            .all()
        )

    def get_latest_date(self, code: str) -> date | None:
        """获取某指数最新估值日期。"""
        return (
            self._db.query(func.max(IndexValuationModel.trade_date))
            .filter(IndexValuationModel.index_code == code)
            .scalar()
        )

    def get_date_range(self, code: str) -> tuple[date | None, date | None]:
        """获取某指数估值的最早和最晚日期。"""
        row = (
            self._db.query(
                func.min(IndexValuationModel.trade_date),
                func.max(IndexValuationModel.trade_date),
            )
            .filter(IndexValuationModel.index_code == code)
            .one()
        )
        return row[0], row[1]

    def find_all_by_code(self, code: str) -> list[IndexValuationModel]:
        """按指数代码查询全部估值记录（升序），供数据质量诊断等场景。"""
        return (
            self._db.query(IndexValuationModel)
            .filter(IndexValuationModel.index_code == code)
            .order_by(IndexValuationModel.trade_date.asc())
            .all()
        )

    def find_by_date_range(
        self,
        start: date,
        end: date,
        index_codes: list[str] | None = None,
    ) -> list[IndexValuationModel]:
        """按日期范围查询全部指数估值（供数据质量检查等场景）。"""
        query = self._db.query(IndexValuationModel).filter(
            and_(
                IndexValuationModel.trade_date >= start,
                IndexValuationModel.trade_date <= end,
            )
        )
        if index_codes:
            query = query.filter(IndexValuationModel.index_code.in_(index_codes))
        return query.all()
