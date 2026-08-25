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
        self, start: date, end: date, index_codes: list[str] | None = None
    ) -> dict[tuple[str, date], IndexDailyBarModel]:
        """按日期范围查询指数日线，返回 (code, date) → 行 的映射。"""
        query = self._db.query(IndexDailyBarModel).filter(
            and_(
                IndexDailyBarModel.trade_date >= start,
                IndexDailyBarModel.trade_date <= end,
            )
        )
        if index_codes:
            query = query.filter(IndexDailyBarModel.index_code.in_(index_codes))
        rows = query.all()
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

    def find_latest_trade_date_before(self, trade_date: date) -> date | None:
        """查询不超过指定日期的最大交易日。

        用于实时模式的有效交易日回退，避免当天数据未就绪时查询为空。

        Args:
            trade_date: 参考日期。

        Returns:
            最新交易日，无数据时返回 None。
        """
        result = (
            self._db.query(IndexDailyBarModel.trade_date)
            .filter(IndexDailyBarModel.trade_date <= trade_date)
            .order_by(IndexDailyBarModel.trade_date.desc())
            .limit(1)
            .first()
        )
        return result[0] if result is not None else None

    def find_trading_dates(
        self,
        start: date,
        end: date,
        index_codes: list[str],
    ) -> list[date]:
        """从指数日线中提取区间内的交易日列表（去重、升序）。

        Args:
            start: 起始日期（含）。
            end: 截止日期（含）。
            index_codes: 指数代码列表。

        Returns:
            交易日列表。
        """
        rows = (
            self._db.query(IndexDailyBarModel.trade_date)
            .filter(
                and_(
                    IndexDailyBarModel.trade_date >= start,
                    IndexDailyBarModel.trade_date <= end,
                    IndexDailyBarModel.index_code.in_(index_codes),
                )
            )
            .distinct()
            .order_by(IndexDailyBarModel.trade_date.asc())
            .all()
        )
        return [r.trade_date for r in rows]

    def find_by_code_date_range(
        self, code: str, start: date, end: date
    ) -> list[IndexDailyBarModel]:
        """按指数代码和日期范围查询日线（升序）。"""
        return (
            self._db.query(IndexDailyBarModel)
            .filter(
                and_(
                    IndexDailyBarModel.index_code == code,
                    IndexDailyBarModel.trade_date >= start,
                    IndexDailyBarModel.trade_date <= end,
                )
            )
            .order_by(IndexDailyBarModel.trade_date.asc())
            .all()
        )

    def get_latest_date(self, code: str) -> date | None:
        """获取某指数最新日线日期。"""
        return (
            self._db.query(func.max(IndexDailyBarModel.trade_date))
            .filter(IndexDailyBarModel.index_code == code)
            .scalar()
        )

    def find_by_date_range(
        self,
        start: date,
        end: date,
        index_codes: list[str] | None = None,
    ) -> list[IndexDailyBarModel]:
        """按日期范围查询全部指数日线（供数据质量检查等场景）。"""
        query = self._db.query(IndexDailyBarModel).filter(
            and_(
                IndexDailyBarModel.trade_date >= start,
                IndexDailyBarModel.trade_date <= end,
            )
        )
        if index_codes:
            query = query.filter(IndexDailyBarModel.index_code.in_(index_codes))
        return query.all()
