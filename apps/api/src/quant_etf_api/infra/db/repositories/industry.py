"""申万行业轮动子系统仓库（只读查询 + 领域写入门禁）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from quant_etf_api.infra.db.models.industry import (
    IndustryDailyBarModel,
    IndustryFactorValueModel,
    IndustryMembershipEventModel,
    IndustryUniverseModel,
    StockDailyCloseModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository


class IndustryUniverseRepository(BaseRepository):
    """industry_universe 只读查询仓库。"""

    def find_all_active(self) -> list[IndustryUniverseModel]:
        """查询全部启用行业，按代码升序。"""
        return (
            self._db.query(IndustryUniverseModel)
            .filter(IndustryUniverseModel.is_active.is_(True))
            .order_by(IndustryUniverseModel.industry_code.asc())
            .all()
        )

    def find_active_codes(self, codes: list[str] | None = None) -> list[str]:
        """查询启用行业代码；codes 非空时取其与启用集的交集。"""
        query = self._db.query(IndustryUniverseModel.industry_code).filter(
            IndustryUniverseModel.is_active.is_(True)
        )
        if codes:
            query = query.filter(IndustryUniverseModel.industry_code.in_(codes))
        rows = query.all()
        return [r[0] for r in rows]

    def find_by_code(self, code: str) -> IndustryUniverseModel | None:
        """按行业代码查询目录行。"""
        return (
            self._db.query(IndustryUniverseModel)
            .filter(IndustryUniverseModel.industry_code == code)
            .first()
        )

    def latest_updated_at(self) -> datetime | None:
        """查询目录最近更新时间，用于判断是否需要补同步。"""
        return self._db.query(func.max(IndustryUniverseModel.updated_at)).scalar()


class IndustryDailyBarRepository(BaseRepository):
    """industry_daily_bar 只读查询仓库。"""

    def find_market_trading_dates(
        self,
        start: date,
        end: date,
    ) -> list[date]:
        """查询区间内行业日线覆盖的市场交易日（去重升序，供日历兜底）。"""
        rows = (
            self._db.query(IndustryDailyBarModel.trade_date)
            .filter(
                and_(
                    IndustryDailyBarModel.trade_date >= start,
                    IndustryDailyBarModel.trade_date <= end,
                )
            )
            .distinct()
            .order_by(IndustryDailyBarModel.trade_date.asc())
            .all()
        )
        return [r[0] for r in rows]

    def find_range(
        self,
        start: date,
        end: date,
        industry_codes: list[str] | None = None,
    ) -> list[IndustryDailyBarModel]:
        """按日期区间查询行业日线（升序）。"""
        query = self._db.query(IndustryDailyBarModel).filter(
            and_(
                IndustryDailyBarModel.trade_date >= start,
                IndustryDailyBarModel.trade_date <= end,
            )
        )
        if industry_codes:
            query = query.filter(IndustryDailyBarModel.industry_code.in_(industry_codes))
        return query.order_by(IndustryDailyBarModel.trade_date.asc()).all()

    def find_trading_dates(
        self,
        start: date,
        end: date,
        industry_codes: list[str],
    ) -> list[date]:
        """查询区间内行业日线覆盖的交易日（去重升序）。"""
        rows = (
            self._db.query(IndustryDailyBarModel.trade_date)
            .filter(
                and_(
                    IndustryDailyBarModel.trade_date >= start,
                    IndustryDailyBarModel.trade_date <= end,
                    IndustryDailyBarModel.industry_code.in_(industry_codes),
                )
            )
            .distinct()
            .order_by(IndustryDailyBarModel.trade_date.asc())
            .all()
        )
        return [r[0] for r in rows]

    def latest_trade_date(self, code: str | None = None) -> date | None:
        """查询单个行业（或全部行业）最新日线日期。"""
        query = self._db.query(func.max(IndustryDailyBarModel.trade_date))
        if code:
            query = query.filter(IndustryDailyBarModel.industry_code == code)
        return query.scalar()

    def bulk_upsert(self, rows: list[dict[str, Any]]) -> int:
        """批量幂等写入行业日线（ON CONFLICT DO NOTHING）。"""
        if not rows:
            return 0
        stmt = (
            pg_insert(IndustryDailyBarModel)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_industry_daily_bar")
        )
        self._db.execute(stmt)
        return len(rows)


class IndustryMembershipEventRepository(BaseRepository):
    """industry_membership_event 只读查询与写入门禁。"""

    def find_events_until(self, end_date: date) -> list[IndustryMembershipEventModel]:
        """查询生效日不晚于 end_date 的全部成分事件。"""
        return (
            self._db.query(IndustryMembershipEventModel)
            .filter(IndustryMembershipEventModel.start_date <= end_date)
            .order_by(
                IndustryMembershipEventModel.stock_code.asc(),
                IndustryMembershipEventModel.start_date.asc(),
            )
            .all()
        )

    def find_all_events(self) -> list[IndustryMembershipEventModel]:
        """查询全部成分事件（供全量重建/导出）。"""
        return self._db.query(IndustryMembershipEventModel).all()

    def latest_fetched_at(self) -> datetime | None:
        """查询成分事件最近入库时间，用于每周刷新判断。"""
        return self._db.query(func.max(IndustryMembershipEventModel.fetched_at)).scalar()

    def bulk_upsert(self, rows: list[dict[str, Any]]) -> int:
        """批量幂等写入成分事件；重复事件仅刷新 source/fetched_at。"""
        if not rows:
            return 0
        stmt = pg_insert(IndustryMembershipEventModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_industry_membership_event",
            set_={"source": stmt.excluded.source, "fetched_at": stmt.excluded.fetched_at},
        )
        self._db.execute(stmt)
        return len(rows)


class StockDailyCloseRepository(BaseRepository):
    """stock_daily_close 只读查询与写入门禁。"""

    def find_trade_dates_by_code(
        self,
        stock_code: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[date]:
        """查询单只股票在日期区间内的全部交易日（升序去重）。"""
        query = self._db.query(StockDailyCloseModel.trade_date).filter(
            StockDailyCloseModel.stock_code == stock_code
        )
        if start is not None:
            query = query.filter(StockDailyCloseModel.trade_date >= start)
        if end is not None:
            query = query.filter(StockDailyCloseModel.trade_date <= end)
        return [r[0] for r in query.order_by(StockDailyCloseModel.trade_date.asc()).all()]

    def delete_by_code(self, stock_code: str) -> int:
        """删除单只股票的全部日线行（供全量重拉使用，调用方负责事务）。"""
        result = (
            self._db.query(StockDailyCloseModel)
            .filter(StockDailyCloseModel.stock_code == stock_code)
            .delete(synchronize_session=False)
        )
        return int(result)

    def find_range(
        self,
        start: date,
        end: date,
        stock_codes: list[str] | None = None,
    ) -> list[StockDailyCloseModel]:
        """按日期区间查询个股收盘（升序）。"""
        query = self._db.query(StockDailyCloseModel).filter(
            and_(
                StockDailyCloseModel.trade_date >= start,
                StockDailyCloseModel.trade_date <= end,
            )
        )
        if stock_codes:
            query = query.filter(StockDailyCloseModel.stock_code.in_(stock_codes))
        return query.order_by(
            StockDailyCloseModel.trade_date.asc(),
            StockDailyCloseModel.stock_code.asc(),
        ).all()

    def trading_dates(self, start: date, end: date) -> list[date]:
        """查询区间内个股收盘覆盖的交易日（去重升序）。"""
        rows = (
            self._db.query(StockDailyCloseModel.trade_date)
            .filter(
                and_(
                    StockDailyCloseModel.trade_date >= start,
                    StockDailyCloseModel.trade_date <= end,
                )
            )
            .distinct()
            .order_by(StockDailyCloseModel.trade_date.asc())
            .all()
        )
        return [r[0] for r in rows]

    def latest_date(self) -> date | None:
        """查询个股收盘最新日期。"""
        return self._db.query(func.max(StockDailyCloseModel.trade_date)).scalar()

    def bulk_upsert(self, rows: list[dict[str, Any]]) -> int:
        """批量幂等写入个股收盘（ON CONFLICT DO NOTHING）。"""
        if not rows:
            return 0
        stmt = (
            pg_insert(StockDailyCloseModel)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_stock_daily_close")
        )
        self._db.execute(stmt)
        return len(rows)


class IndustryFactorValueRepository(BaseRepository):
    """industry_factor_value 只读查询与写入门禁。"""

    def find_values(
        self,
        factor_id: str,
        start: date,
        end: date,
        industry_codes: list[str] | None = None,
    ) -> list[IndustryFactorValueModel]:
        """查询指定因子在日期区间的行业因子行（升序）。"""
        query = self._db.query(IndustryFactorValueModel).filter(
            and_(
                IndustryFactorValueModel.factor_id == factor_id,
                IndustryFactorValueModel.trade_date >= start,
                IndustryFactorValueModel.trade_date <= end,
            )
        )
        if industry_codes:
            query = query.filter(IndustryFactorValueModel.industry_code.in_(industry_codes))
        return query.order_by(IndustryFactorValueModel.trade_date.asc()).all()

    def find_latest_trade_date(self, factor_id: str) -> date | None:
        """查询某行业因子最新日期。"""
        return (
            self._db.query(func.max(IndustryFactorValueModel.trade_date))
            .filter(IndustryFactorValueModel.factor_id == factor_id)
            .scalar()
        )

    def bulk_upsert(self, rows: list[dict[str, Any]]) -> int:
        """批量幂等写入行业因子值（重复行更新数值与 payload）。"""
        if not rows:
            return 0
        stmt = pg_insert(IndustryFactorValueModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_industry_factor_value",
            set_={
                "factor_value_numeric": stmt.excluded.factor_value_numeric,
                "factor_payload": stmt.excluded.factor_payload,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        self._db.execute(stmt)
        return len(rows)
