"""申万行业轮动子系统仓库（只读查询 + 领域写入门禁）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from quant_etf_api.domain.industry.membership import (
    current_membership_counts,
    normalize_event_rows,
)
from quant_etf_api.infra.db.models.industry import (
    IndustryDailyBarModel,
    IndustryMembershipEventModel,
    IndustryUniverseModel,
    StockDailyCloseModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository

# 单条 PG 批量 INSERT 的行数上限：按 500 行切块执行，避免单语句参数过多
# （数万参数）触发数据库服务端断连/超时等 OperationalError
_PG_INSERT_CHUNK_SIZE = 500


def _exec_pg_insert_chunks(
    db: Session,
    rows: list[dict[str, Any]],
    builder: Callable[[list[dict[str, Any]]], Any],
) -> int:
    """把多行 PG INSERT/ON CONFLICT 语句按固定大小分块执行。

    Args:
        db: SQLAlchemy Session。
        rows: 待写入的字典行列表。
        builder: 根据行块构造最终语句的可调用对象。

    Returns:
        参与写入的总行数（与输入一致）。
    """
    if not rows:
        return 0
    for start in range(0, len(rows), _PG_INSERT_CHUNK_SIZE):
        db.execute(builder(rows[start : start + _PG_INSERT_CHUNK_SIZE]))
    return len(rows)


class IndustryUniverseRepository(BaseRepository):
    """industry_universe 只读查询仓库。"""

    def find_all(self) -> list[IndustryUniverseModel]:
        """查询全部行业（含停用），按代码升序。"""
        return (
            self._db.query(IndustryUniverseModel)
            .order_by(IndustryUniverseModel.industry_code.asc())
            .all()
        )

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

    def find_by_codes(self, codes: list[str]) -> list[IndustryUniverseModel]:
        """按行业代码列表查询目录行（保持传入顺序）。"""
        if not codes:
            return []
        rows = {
            row.industry_code: row
            for row in self._db.query(IndustryUniverseModel)
            .filter(IndustryUniverseModel.industry_code.in_(codes))
            .all()
        }
        return [rows[code] for code in codes if code in rows]

    def latest_updated_at(self) -> datetime | None:
        """查询目录最近更新时间，用于判断是否需要补同步。"""
        return self._db.query(func.max(IndustryUniverseModel.updated_at)).scalar()

    def bulk_upsert(
        self,
        rows: list[dict[str, Any]],
        update_cols: set[str],
    ) -> int:
        """批量幂等写入行业目录；冲突时仅更新指定列（含质量快照列）。"""
        if not rows:
            return 0
        stmt = pg_insert(IndustryUniverseModel).values(rows)
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=[IndustryUniverseModel.industry_code],
                set_={
                    col: getattr(stmt.excluded, col)
                    for col in update_cols
                    if col != "industry_code"
                },
            )
        else:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[IndustryUniverseModel.industry_code]
            )
        self._db.execute(stmt)
        return len(rows)


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

    def find_by_code(self, industry_code: str) -> list[IndustryDailyBarModel]:
        """查询单个行业的全部日线行（升序，供字段质量统计与全量处理）。"""
        return (
            self._db.query(IndustryDailyBarModel)
            .filter(IndustryDailyBarModel.industry_code == industry_code)
            .order_by(IndustryDailyBarModel.trade_date.asc())
            .all()
        )

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

    def find_trade_dates_by_code(
        self,
        industry_code: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[date]:
        """查询单个行业在日期区间内的全部交易日（升序去重）。"""
        query = self._db.query(IndustryDailyBarModel.trade_date).filter(
            IndustryDailyBarModel.industry_code == industry_code
        )
        if start is not None:
            query = query.filter(IndustryDailyBarModel.trade_date >= start)
        if end is not None:
            query = query.filter(IndustryDailyBarModel.trade_date <= end)
        return [r[0] for r in query.order_by(IndustryDailyBarModel.trade_date.asc()).all()]

    def find_latest_bars(
        self,
        industry_codes: list[str] | None = None,
    ) -> dict[str, IndustryDailyBarModel]:
        """查询各行业最新一根日线（行业代码 → 行）。"""
        subq = (
            self._db.query(
                IndustryDailyBarModel.industry_code.label("industry_code"),
                func.max(IndustryDailyBarModel.trade_date).label("max_date"),
            )
            .group_by(IndustryDailyBarModel.industry_code)
            .subquery()
        )
        query = self._db.query(IndustryDailyBarModel).join(
            subq,
            and_(
                IndustryDailyBarModel.industry_code == subq.c.industry_code,
                IndustryDailyBarModel.trade_date == subq.c.max_date,
            ),
        )
        if industry_codes:
            query = query.filter(IndustryDailyBarModel.industry_code.in_(industry_codes))
        return {row.industry_code: row for row in query.all()}

    def find_close_before(
        self,
        industry_code: str,
        before_date: date,
    ) -> float | None:
        """查询某行业在指定日期之前最近一根日线的收盘价。"""
        row = (
            self._db.query(IndustryDailyBarModel)
            .filter(
                and_(
                    IndustryDailyBarModel.industry_code == industry_code,
                    IndustryDailyBarModel.trade_date < before_date,
                )
            )
            .order_by(IndustryDailyBarModel.trade_date.desc())
            .first()
        )
        return row.close_price if row is not None else None

    def delete_by_code(self, industry_code: str) -> int:
        """删除单个行业的全部日线行（供全量重拉使用，调用方负责事务）。"""
        result = (
            self._db.query(IndustryDailyBarModel)
            .filter(IndustryDailyBarModel.industry_code == industry_code)
            .delete(synchronize_session=False)
        )
        return int(result)

    def bulk_upsert(self, rows: list[dict[str, Any]]) -> int:
        """批量幂等写入行业日线；冲突时刷新行情与派生字段。

        派生字段（prev_close_price/change_pct）由调用方按同一行业代码升序
        计算后传入；重复行做 DO UPDATE，保证历史行也能被补上派生列。
        """
        if not rows:
            return 0

        def _build(chunk: list[dict[str, Any]]) -> Any:
            """构造带派生字段更新的单块 ON CONFLICT DO UPDATE 语句。"""
            stmt = pg_insert(IndustryDailyBarModel).values(chunk)
            return stmt.on_conflict_do_update(
                constraint="uq_industry_daily_bar",
                set_={
                    "open_price": stmt.excluded.open_price,
                    "high_price": stmt.excluded.high_price,
                    "low_price": stmt.excluded.low_price,
                    "close_price": stmt.excluded.close_price,
                    "prev_close_price": stmt.excluded.prev_close_price,
                    "change_pct": stmt.excluded.change_pct,
                    "volume": stmt.excluded.volume,
                    "turnover": stmt.excluded.turnover,
                    "source": stmt.excluded.source,
                    "ingested_at": stmt.excluded.ingested_at,
                },
            )

        return _exec_pg_insert_chunks(self._db, rows, _build)


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

    def current_membership_counts(self, end_date: date) -> dict[str, int]:
        """统计 end_date 时各一级行业的有效成分股数量。

        每只股票取 start_date 不晚于 end_date 的最新成分事件，再按一级行业
        汇总去重后的数量（与扩散重建当日有效成分的口径一致）。

        Args:
            end_date: 成分归属生效截止日（含）。

        Returns:
            行业代码 → 成分股数量字典；无有效成分时为空字典。
        """
        rows = (
            self._db.query(
                IndustryMembershipEventModel.stock_code,
                IndustryMembershipEventModel.industry_code,
                IndustryMembershipEventModel.start_date,
            )
            .filter(IndustryMembershipEventModel.start_date <= end_date)
            .all()
        )
        events = normalize_event_rows(rows)
        return current_membership_counts(events, end_date=end_date)

    def bulk_upsert(self, rows: list[dict[str, Any]]) -> int:
        """批量幂等写入成分事件；重复事件仅刷新 source/fetched_at。"""
        if not rows:
            return 0

        def _build(chunk: list[dict[str, Any]]) -> Any:
            """构造单块成分事件 ON CONFLICT DO UPDATE 语句。"""
            stmt = pg_insert(IndustryMembershipEventModel).values(chunk)
            return stmt.on_conflict_do_update(
                constraint="uq_industry_membership_event",
                set_={
                    "source": stmt.excluded.source,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )

        return _exec_pg_insert_chunks(self._db, rows, _build)


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

    def find_close_rows(
        self,
        start: date,
        end: date,
        stock_codes: list[str],
    ) -> list[Any]:
        """按股票列表查询收盘三列轻量行（trade_date, stock_code, close）。

        供扩散指标按行业分批计算使用：只投影必需列，避免把整行 ORM 对象
        载入内存（P02 内存优化的读取侧改造）。

        Args:
            start: 起始日期（含）。
            end: 结束日期（含）。
            stock_codes: 股票代码列表。

        Returns:
            (trade_date, stock_code, close) 元组列表，按日期、股票升序。
        """
        if not stock_codes:
            return []
        return list(
            self._db.query(
                StockDailyCloseModel.trade_date,
                StockDailyCloseModel.stock_code,
                StockDailyCloseModel.close,
            )
            .filter(
                and_(
                    StockDailyCloseModel.trade_date >= start,
                    StockDailyCloseModel.trade_date <= end,
                    StockDailyCloseModel.stock_code.in_(stock_codes),
                )
            )
            .order_by(
                StockDailyCloseModel.trade_date.asc(),
                StockDailyCloseModel.stock_code.asc(),
            )
            .all()
        )

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

        def _build(chunk: list[dict[str, Any]]) -> Any:
            """构造单块个股收盘 ON CONFLICT DO NOTHING 语句。"""
            return (
                pg_insert(StockDailyCloseModel)
                .values(chunk)
                .on_conflict_do_nothing(constraint="uq_stock_daily_close")
            )

        return _exec_pg_insert_chunks(self._db, rows, _build)
