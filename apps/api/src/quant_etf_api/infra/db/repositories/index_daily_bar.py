"""指数日线行情仓库。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func

from quant_etf_api.infra.db.models.core import IndexDailyBarModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class IndexDailyBarRepository(BaseRepository):
    """IndexDailyBarModel 的只读查询仓库。"""

    def find_by_code_limit(self, code: str, limit: int) -> list[IndexDailyBarModel]:
        """按指数代码查询最近 N 条日线（升序）。"""
        rows = (
            self._db.query(IndexDailyBarModel)
            .filter(IndexDailyBarModel.index_code == code)
            .order_by(IndexDailyBarModel.trade_date.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

    def find_all_date_range(
        self, start: date, end: date
    ) -> dict[tuple[str, date], IndexDailyBarModel]:
        """按日期范围查询所有指数日线，返回 (code, date) → 行 的映射。"""
        rows = (
            self._db.query(IndexDailyBarModel)
            .filter(
                and_(
                    IndexDailyBarModel.trade_date >= start,
                    IndexDailyBarModel.trade_date <= end,
                )
            )
            .all()
        )
        return {(r.index_code, r.trade_date): r for r in rows}

    def get_date_range(self, code: str) -> tuple[date | None, date | None]:
        """获取某指数日线的最早和最晚日期。"""
        row = (
            self._db.query(
                func.min(IndexDailyBarModel.trade_date),
                func.max(IndexDailyBarModel.trade_date),
            )
            .filter(IndexDailyBarModel.index_code == code)
            .one()
        )
        return row[0], row[1]
