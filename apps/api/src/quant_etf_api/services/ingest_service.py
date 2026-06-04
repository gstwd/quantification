from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.akshare_fund import AkShareFundClient
from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.clients.akshare_macro import AkShareMacroClient
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    EtfDailyBarModel,
    EtfDailyShareModel,
    IndexDailyBarModel,
    IndexValuationModel,
    MacroIndicatorModel,
    ResearchRunItemModel,
    ResearchRunModel,
)
from quant_etf_api.infra.db.repositories.etf_universe import EtfUniverseRepository
from quant_etf_api.schemas.market_data import (
    BenchmarkIndex,
    DailyBar,
    IndexValuation,
    MacroIndicatorSchema,
    ShareSnapshot,
)

logger = logging.getLogger(__name__)

# 每个资源独立一把锁，防止并发冷启动时多线程重复拉取同一外部 API
_fetch_locks: dict[str, threading.Lock] = {}
_fetch_locks_meta = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _fetch_locks_meta:
        if key not in _fetch_locks:
            _fetch_locks[key] = threading.Lock()
        return _fetch_locks[key]


# ────────────────────────── ETF 行 → Schema ──────────────────────────


def _bar_row_to_schema(row: EtfDailyBarModel) -> DailyBar:
    return DailyBar(
        trade_date=row.trade_date,
        code=row.etf_code,
        open_price=row.open_price,
        high_price=row.high_price,
        low_price=row.low_price,
        close_price=row.close_price,
        change_pct=row.change_pct,
        volume=row.volume,
        turnover=row.turnover,
        source=row.source,
        ingested_at=row.ingested_at,
    )


def _share_row_to_schema(row: EtfDailyShareModel) -> ShareSnapshot:
    return ShareSnapshot(
        trade_date=row.trade_date,
        etf_code=row.etf_code,
        shares_total=row.shares_total,
        shares_delta=row.shares_delta,
        shares_delta_pct=row.shares_delta_pct,
        nav=row.nav,
        aum=row.aum,
        source=row.source,
        ingested_at=row.ingested_at,
    )


# ──────────────────────── 指数行 → Schema ────────────────────────────


def _index_bar_row_to_schema(row: IndexDailyBarModel) -> DailyBar:
    return DailyBar(
        trade_date=row.trade_date,
        code=row.index_code,
        open_price=row.open_price,
        high_price=row.high_price,
        low_price=row.low_price,
        close_price=row.close_price,
        change_pct=row.change_pct,
        volume=row.volume,
        turnover=row.turnover,
        source=row.source,
        ingested_at=row.ingested_at,
    )


def _index_valuation_row_to_schema(row: IndexValuationModel) -> IndexValuation:
    return IndexValuation(
        trade_date=row.trade_date,
        index_code=row.index_code,
        pe=row.pe,
        pe_percentile=row.pe_percentile,
        pb=row.pb,
        pb_percentile=row.pb_percentile,
        dividend_yield=row.dividend_yield,
        source=row.source,
    )


def _macro_row_to_schema(row: MacroIndicatorModel) -> MacroIndicatorSchema:
    return MacroIndicatorSchema(
        indicator_code=row.indicator_code,
        indicator_name=row.indicator_name,
        period=row.period,
        value=row.value,
        unit=row.unit,
        source=row.source,
    )


# ──────────────────────────── IngestService ──────────────────────────


class IngestService:
    """数据摄取服务。

    提供 ETF 行情 / 份额 / 指数日线 / 指数估值 / 宏观指标的数据拉取、
    幂等写入和读穿透缓存，供 API 路由和定时调度器使用。
    """

    def __init__(self, db: Session, universe_repo: EtfUniverseRepository | None = None) -> None:
        self._db = db
        self._universe_repo = universe_repo or EtfUniverseRepository(db)

    def latest_trade_date(self) -> date:
        return date.today()

    # ==================================================================
    # ETF 日线
    # ==================================================================

    def _fetch_and_upsert_bars_full_history(self, etf_code: str) -> int:
        """冷启动：一次性拉取 ETF 全量历史日线并幂等写入。

        使用新浪后端（fund_etf_hist_sina），单次调用返回从上市至今的全量数据，
        无需按年分批（约 425ms 完成，不受东方财富代理封锁影响）。
        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制。

        Returns:
            写入记录数
        """
        client = AkShareFundClient()
        try:
            bars = client.fetch_etf_daily_bars(etf_code)
        except Exception as exc:
            logger.warning("ETF %s 全量日线拉取失败: %s", etf_code, exc)
            return 0

        if not bars:
            return 0

        batch_size = 5000
        values = [
            {
                "trade_date": b.trade_date,
                "etf_code": etf_code,
                "open_price": b.open_price,
                "high_price": b.high_price,
                "low_price": b.low_price,
                "close_price": b.close_price,
                "volume": b.volume,
                "turnover": b.turnover,
                "change_pct": b.change_pct,
                "amplitude": b.amplitude,
                "source": "akshare",
                "ingested_at": datetime.now(timezone.utc),
            }
            for b in bars
        ]
        for i in range(0, len(values), batch_size):
            batch = values[i : i + batch_size]
            stmt = (
                insert(EtfDailyBarModel)
                .values(batch)
                .on_conflict_do_nothing(constraint="uq_etf_daily_bar")
            )
            self._db.execute(stmt)
        self._db.commit()
        return len(bars)

    def _fetch_and_upsert_bars_incremental(self, etf_code: str) -> str:
        """增量补全 ETF 日线：仅拉取 DB 最新日期之后的缺失数据。

        若 DB 已有当日（或更新）数据则直接跳过 API 调用，避免重复拉取。
        若 DB 无任何历史数据则触发全量拉取。

        Returns:
            API 返回的最新交易日日期字符串；无数据时返回空字符串
        """
        today = date.today()
        max_date: date | None = (
            self._db.query(func.max(EtfDailyBarModel.trade_date))
            .filter(EtfDailyBarModel.etf_code == etf_code)
            .scalar()
        )

        # DB 已有当日或更新的数据，无需拉取
        if max_date is not None and max_date >= today:
            return str(max_date)

        # 无历史数据，走全量拉取
        if max_date is None:
            self._fetch_and_upsert_bars_full_history(etf_code)
            refreshed: date | None = (
                self._db.query(func.max(EtfDailyBarModel.trade_date))
                .filter(EtfDailyBarModel.etf_code == etf_code)
                .scalar()
            )
            return str(refreshed) if refreshed else ""

        # 有历史数据，拉取 max_date 到今日的缺口数据
        start_str = (max_date - timedelta(days=1)).strftime("%Y%m%d")
        end_str = today.strftime("%Y%m%d")
        bars = AkShareFundClient().fetch_etf_daily_bars(
            etf_code, start_date=start_str, end_date=end_str
        )
        if not bars:
            return str(max_date)
        stmt = (
            insert(EtfDailyBarModel)
            .values(
                [
                    {
                        "trade_date": b.trade_date,
                        "etf_code": etf_code,
                        "open_price": b.open_price,
                        "high_price": b.high_price,
                        "low_price": b.low_price,
                        "close_price": b.close_price,
                        "volume": b.volume,
                        "turnover": b.turnover,
                        "change_pct": b.change_pct,
                        "amplitude": b.amplitude,
                        "source": "akshare",
                        "ingested_at": datetime.now(timezone.utc),
                    }
                    for b in bars
                ]
            )
            .on_conflict_do_nothing(constraint="uq_etf_daily_bar")
        )
        self._db.execute(stmt)
        self._db.commit()
        return str(bars[-1].trade_date)

    def _query_etf_bars(
        self,
        etf_code: str,
        limit: int,
        start_date: date | None,
        end_date: date | None,
    ) -> list[EtfDailyBarModel]:
        """构建 ETF 日线查询（日期范围模式或 limit 模式）。"""
        q = self._db.query(EtfDailyBarModel).filter(EtfDailyBarModel.etf_code == etf_code)
        if start_date and end_date:
            return (
                q.filter(
                    EtfDailyBarModel.trade_date >= start_date,
                    EtfDailyBarModel.trade_date <= end_date,
                )
                .order_by(EtfDailyBarModel.trade_date.asc())
                .all()
            )
        rows = q.order_by(EtfDailyBarModel.trade_date.desc()).limit(limit).all()
        return list(reversed(rows))

    def get_daily_bars(
        self,
        etf_code: str,
        limit: int = 250,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[DailyBar]:
        """ETF 日线读穿透缓存。

        提供 start_date/end_date 时使用日期范围查询，否则使用 limit。
        """
        try:
            rows = self._query_etf_bars(etf_code, limit, start_date, end_date)
            if rows:
                return [_bar_row_to_schema(r) for r in rows]

            with _get_lock(f"bars:{etf_code}"):
                rows = self._query_etf_bars(etf_code, limit, start_date, end_date)
                if rows:
                    return [_bar_row_to_schema(r) for r in rows]

                self._fetch_and_upsert_bars_full_history(etf_code)
                rows = self._query_etf_bars(etf_code, limit, start_date, end_date)
                if rows:
                    return [_bar_row_to_schema(r) for r in rows]
        except Exception:
            logger.warning("get_daily_bars failed for %s", etf_code, exc_info=True)
            self._db.rollback()
        return []

    # ==================================================================
    # ETF 份额
    # ==================================================================

    def _fetch_and_upsert_shares(self, etf_code: str, trade_date: date) -> None:
        """从 AkShare 拉取 ETF 份额快照并幂等写入 DB。

        写入后自动计算与前一日份额的 delta 和 delta_pct。
        """
        snapshot = AkShareFundClient().fetch_share_snapshot(etf_code)
        if snapshot is None:
            raise ValueError(f"无法获取 {etf_code} 的份额数据")
        stmt = (
            insert(EtfDailyShareModel)
            .values(
                [
                    {
                        "trade_date": trade_date,
                        "etf_code": etf_code,
                        "shares_total": snapshot.shares_total,
                        "aum": snapshot.aum,
                        "nav": round(snapshot.price, 3),
                        "source": "akshare",
                        "ingested_at": datetime.now(timezone.utc),
                    }
                ]
            )
            .on_conflict_do_nothing(constraint="uq_etf_daily_share")
        )
        self._db.execute(stmt)
        self._db.commit()

        # 计算与前一日份额的差值
        prev = (
            self._db.query(EtfDailyShareModel)
            .filter(
                EtfDailyShareModel.etf_code == etf_code,
                EtfDailyShareModel.trade_date < trade_date,
            )
            .order_by(EtfDailyShareModel.trade_date.desc())
            .first()
        )
        if prev is not None and prev.shares_total is not None and snapshot.shares_total > 0:
            delta = round(snapshot.shares_total - prev.shares_total, 2)
            delta_pct = round(delta / prev.shares_total * 100, 2) if prev.shares_total > 0 else None
            row = (
                self._db.query(EtfDailyShareModel)
                .filter_by(etf_code=etf_code, trade_date=trade_date)
                .first()
            )
            if row is not None:
                row.shares_delta = delta
                row.shares_delta_pct = delta_pct
                self._db.commit()

    def _query_shares(
        self,
        etf_code: str,
        limit: int,
        start_date: date | None,
        end_date: date | None,
    ) -> list[EtfDailyShareModel]:
        """构建 ETF 份额查询（日期范围模式或 limit 模式）。"""
        q = self._db.query(EtfDailyShareModel).filter(EtfDailyShareModel.etf_code == etf_code)
        if start_date and end_date:
            return (
                q.filter(
                    EtfDailyShareModel.trade_date >= start_date,
                    EtfDailyShareModel.trade_date <= end_date,
                )
                .order_by(EtfDailyShareModel.trade_date.asc())
                .all()
            )
        rows = q.order_by(EtfDailyShareModel.trade_date.desc()).limit(limit).all()
        return list(reversed(rows))

    def get_share_history(
        self,
        etf_code: str,
        limit: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[ShareSnapshot]:
        """ETF 份额读穿透缓存。"""
        try:
            rows = self._query_shares(etf_code, limit, start_date, end_date)
            if rows:
                return [_share_row_to_schema(r) for r in rows]

            with _get_lock(f"shares:{etf_code}"):
                rows = self._query_shares(etf_code, limit, start_date, end_date)
                if rows:
                    return [_share_row_to_schema(r) for r in rows]

                self._fetch_and_upsert_shares(etf_code, date.today())
                rows = self._query_shares(etf_code, limit, start_date, end_date)
                if rows:
                    return [_share_row_to_schema(r) for r in rows]
        except Exception:
            logger.warning("get_share_history failed for %s", etf_code, exc_info=True)
            self._db.rollback()
        return []

    # ==================================================================
    # 指数日线（AkShare）
    # ==================================================================

    def _fetch_and_upsert_index_bars(self, index_code: str) -> int:
        """从 AkShare 拉取指数日线并幂等写入 index_daily_bar。

        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制
        （每行 10 字段，批次上限 6000 行 = 60000 参数）。

        Returns:
            写入记录数
        """
        bars = AkShareIndexClient().fetch_index_daily(index_code)
        if not bars:
            return 0

        batch_size = 6000
        values = [
            {
                "trade_date": b.trade_date,
                "index_code": index_code,
                "open_price": b.open_price,
                "high_price": b.high_price,
                "low_price": b.low_price,
                "close_price": b.close_price,
                "volume": b.volume,
                "turnover": b.turnover,
                "source": "akshare",
                "ingested_at": datetime.now(timezone.utc),
            }
            for b in bars
        ]
        for i in range(0, len(values), batch_size):
            batch = values[i : i + batch_size]
            stmt = (
                insert(IndexDailyBarModel)
                .values(batch)
                .on_conflict_do_nothing(constraint="uq_index_daily_bar")
            )
            self._db.execute(stmt)
        self._db.commit()
        return len(bars)

    def get_benchmark_indexes(self) -> list[BenchmarkIndex]:
        """返回所有基准指数（从种子表读取）。"""
        rows = self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()
        return [BenchmarkIndex(index_code=r.index_code, index_name=r.name_cn) for r in rows]

    def _query_index_bars(
        self,
        index_code: str,
        limit: int,
        start_date: date | None,
        end_date: date | None,
    ) -> list[IndexDailyBarModel]:
        """构建指数日线查询（日期范围模式或 limit 模式）。"""
        q = self._db.query(IndexDailyBarModel).filter(IndexDailyBarModel.index_code == index_code)
        if start_date and end_date:
            return (
                q.filter(
                    IndexDailyBarModel.trade_date >= start_date,
                    IndexDailyBarModel.trade_date <= end_date,
                )
                .order_by(IndexDailyBarModel.trade_date.asc())
                .all()
            )
        rows = q.order_by(IndexDailyBarModel.trade_date.desc()).limit(limit).all()
        return list(reversed(rows))

    def get_index_daily_bars(
        self,
        index_code: str,
        limit: int = 250,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[DailyBar]:
        """指数日线读穿透缓存。"""
        try:
            rows = self._query_index_bars(index_code, limit, start_date, end_date)
            if rows:
                return [_index_bar_row_to_schema(r) for r in rows]

            with _get_lock(f"index_bars:{index_code}"):
                rows = self._query_index_bars(index_code, limit, start_date, end_date)
                if rows:
                    return [_index_bar_row_to_schema(r) for r in rows]

                self._fetch_and_upsert_index_bars(index_code)
                rows = self._query_index_bars(index_code, limit, start_date, end_date)
                if rows:
                    return [_index_bar_row_to_schema(r) for r in rows]
        except Exception:
            logger.warning(
                "get_index_daily_bars failed for %s, returning []", index_code, exc_info=True
            )
            self._db.rollback()

        return []

    # ==================================================================
    # 指数估值 PE/PB（AkShare）
    # ==================================================================

    def _fetch_and_upsert_index_valuation(self, index_code: str) -> int:
        """从 AkShare 拉取指数 PE/PB 估值并幂等写入 index_valuation。

        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制
        （每行 9 字段，批次上限 7000 行 = 63000 参数）。

        Returns:
            写入记录数
        """
        valuations = AkShareIndexClient().fetch_index_valuation(index_code)
        if not valuations:
            return 0

        batch_size = 7000
        values = [
            {
                "trade_date": v.trade_date,
                "index_code": index_code,
                "pe": v.pe,
                "pe_percentile": v.pe_percentile,
                "pb": v.pb,
                "pb_percentile": v.pb_percentile,
                "dividend_yield": v.dividend_yield,
                "source": "akshare",
                "ingested_at": datetime.now(timezone.utc),
            }
            for v in valuations
        ]
        for i in range(0, len(values), batch_size):
            batch = values[i : i + batch_size]
            stmt = (
                insert(IndexValuationModel)
                .values(batch)
                .on_conflict_do_nothing(constraint="uq_index_valuation")
            )
            self._db.execute(stmt)
        self._db.commit()
        return len(valuations)

    def _query_index_valuation(
        self,
        index_code: str,
        limit: int,
        start_date: date | None,
        end_date: date | None,
    ) -> list[IndexValuationModel]:
        """构建指数估值查询（日期范围模式或 limit 模式）。"""
        q = self._db.query(IndexValuationModel).filter(IndexValuationModel.index_code == index_code)
        if start_date and end_date:
            return (
                q.filter(
                    IndexValuationModel.trade_date >= start_date,
                    IndexValuationModel.trade_date <= end_date,
                )
                .order_by(IndexValuationModel.trade_date.asc())
                .all()
            )
        rows = q.order_by(IndexValuationModel.trade_date.desc()).limit(limit).all()
        return list(reversed(rows))

    def get_index_valuation(
        self,
        index_code: str,
        limit: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[IndexValuation]:
        """指数估值读穿透缓存。

        提供 start_date/end_date 时使用日期范围查询，否则使用 limit。
        """
        try:
            rows = self._query_index_valuation(index_code, limit, start_date, end_date)
            if rows:
                return [_index_valuation_row_to_schema(r) for r in rows]

            with _get_lock(f"index_valuation:{index_code}"):
                rows = self._query_index_valuation(index_code, limit, start_date, end_date)
                if rows:
                    return [_index_valuation_row_to_schema(r) for r in rows]

                self._fetch_and_upsert_index_valuation(index_code)
                rows = self._query_index_valuation(index_code, limit, start_date, end_date)
                if rows:
                    return [_index_valuation_row_to_schema(r) for r in rows]
        except Exception:
            logger.warning(
                "get_index_valuation failed for %s, returning []", index_code, exc_info=True
            )
            self._db.rollback()

        return []

    # ==================================================================
    # 日期范围元数据
    # ==================================================================

    def get_etf_date_range(self, etf_code: str) -> tuple[date | None, date | None]:
        """查询 ETF 日线数据的最早和最晚日期。"""
        row = (
            self._db.query(
                func.min(EtfDailyBarModel.trade_date),
                func.max(EtfDailyBarModel.trade_date),
            )
            .filter(EtfDailyBarModel.etf_code == etf_code)
            .one()
        )
        return row[0], row[1]

    def get_index_date_range(self, index_code: str) -> tuple[date | None, date | None]:
        """查询指数日线数据的最早和最晚日期。"""
        row = (
            self._db.query(
                func.min(IndexDailyBarModel.trade_date),
                func.max(IndexDailyBarModel.trade_date),
            )
            .filter(IndexDailyBarModel.index_code == index_code)
            .one()
        )
        return row[0], row[1]

    # ==================================================================
    # 宏观指标（AkShare）
    # ==================================================================

    def _fetch_and_upsert_macro(self) -> int:
        """从 AkShare 拉取所有宏观指标（CPI/PMI/LPR）并幂等写入 macro_indicator。

        Returns:
            写入记录数
        """
        indicators = AkShareMacroClient().fetch_all()
        if not indicators:
            return 0
        stmt = (
            insert(MacroIndicatorModel)
            .values(
                [
                    {
                        "indicator_code": i.indicator_code,
                        "indicator_name": i.indicator_name,
                        "period": i.period,
                        "value": i.value,
                        "unit": i.unit,
                        "source": "akshare",
                        "ingested_at": datetime.now(timezone.utc),
                    }
                    for i in indicators
                ]
            )
            .on_conflict_do_nothing(constraint="uq_macro_indicator")
        )
        self._db.execute(stmt)
        self._db.commit()
        return len(indicators)

    def get_macro_indicators(
        self, indicator_code: str, limit: int = 60
    ) -> list[MacroIndicatorSchema]:
        """宏观指标读穿透缓存。"""
        try:
            rows = (
                self._db.query(MacroIndicatorModel)
                .filter(MacroIndicatorModel.indicator_code == indicator_code)
                .order_by(MacroIndicatorModel.period.desc())
                .limit(limit)
                .all()
            )
            if rows:
                return [_macro_row_to_schema(r) for r in reversed(rows)]

            with _get_lock(f"macro:{indicator_code}"):
                rows = (
                    self._db.query(MacroIndicatorModel)
                    .filter(MacroIndicatorModel.indicator_code == indicator_code)
                    .order_by(MacroIndicatorModel.period.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_macro_row_to_schema(r) for r in reversed(rows)]

                self._fetch_and_upsert_macro()
                rows = (
                    self._db.query(MacroIndicatorModel)
                    .filter(MacroIndicatorModel.indicator_code == indicator_code)
                    .order_by(MacroIndicatorModel.period.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_macro_row_to_schema(r) for r in reversed(rows)]
        except Exception:
            logger.warning(
                "get_macro_indicators failed for %s, returning []", indicator_code, exc_info=True
            )
            self._db.rollback()

        return []

    # ==================================================================
    # 数据质量检查
    # ==================================================================

    def check_data_freshness(self) -> dict[str, Any]:
        """检查各数据表的新鲜度和覆盖率。

        针对每个活跃 ETF / 基准指数，检查对应数据表中是否有记录、
        最新数据日期距今是否超过 3 个自然日（节假日容忍），返回汇总结果。
        """
        today = date.today()
        stale_threshold = today - timedelta(days=3)
        result: dict = {}

        etfs = self._universe_repo.find_all_active()

        # --- ETF 日线 ---
        bar_stale = []
        bar_missing = []
        bar_latest: date | None = None
        for etf in etfs:
            max_d = (
                self._db.query(func.max(EtfDailyBarModel.trade_date))
                .filter(EtfDailyBarModel.etf_code == etf.etf_code)
                .scalar()
            )
            if max_d is None:
                bar_missing.append(
                    {
                        "code": etf.etf_code,
                        "name": etf.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
            else:
                if bar_latest is None or max_d > bar_latest:
                    bar_latest = max_d
                if max_d < stale_threshold:
                    bar_stale.append(
                        {
                            "code": etf.etf_code,
                            "name": etf.name_cn,
                            "latest_date": str(max_d),
                            "is_stale": True,
                        }
                    )

        result["etf_bars"] = {
            "total": len(etfs),
            "up_to_date": len(etfs) - len(bar_stale) - len(bar_missing),
            "stale": bar_stale,
            "missing": bar_missing,
            "latest_date": str(bar_latest) if bar_latest else None,
        }

        # --- ETF 份额 ---
        share_stale = []
        share_missing = []
        share_latest: date | None = None
        for etf in etfs:
            max_d = (
                self._db.query(func.max(EtfDailyShareModel.trade_date))
                .filter(EtfDailyShareModel.etf_code == etf.etf_code)
                .scalar()
            )
            if max_d is None:
                share_missing.append(
                    {
                        "code": etf.etf_code,
                        "name": etf.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
            else:
                if share_latest is None or max_d > share_latest:
                    share_latest = max_d
                if max_d < stale_threshold:
                    share_stale.append(
                        {
                            "code": etf.etf_code,
                            "name": etf.name_cn,
                            "latest_date": str(max_d),
                            "is_stale": True,
                        }
                    )

        result["etf_shares"] = {
            "total": len(etfs),
            "up_to_date": len(etfs) - len(share_stale) - len(share_missing),
            "stale": share_stale,
            "missing": share_missing,
            "latest_date": str(share_latest) if share_latest else None,
        }

        # --- 指数日线 ---
        indexes = self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()
        idx_bar_stale = []
        idx_bar_missing = []
        idx_bar_latest: date | None = None
        for idx in indexes:
            max_d = (
                self._db.query(func.max(IndexDailyBarModel.trade_date))
                .filter(IndexDailyBarModel.index_code == idx.index_code)
                .scalar()
            )
            if max_d is None:
                idx_bar_missing.append(
                    {
                        "code": idx.index_code,
                        "name": idx.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
            else:
                if idx_bar_latest is None or max_d > idx_bar_latest:
                    idx_bar_latest = max_d
                if max_d < stale_threshold:
                    idx_bar_stale.append(
                        {
                            "code": idx.index_code,
                            "name": idx.name_cn,
                            "latest_date": str(max_d),
                            "is_stale": True,
                        }
                    )

        result["index_bars"] = {
            "total": len(indexes),
            "up_to_date": len(indexes) - len(idx_bar_stale) - len(idx_bar_missing),
            "stale": idx_bar_stale,
            "missing": idx_bar_missing,
            "latest_date": str(idx_bar_latest) if idx_bar_latest else None,
        }

        # --- 指数估值 ---
        idx_val_stale = []
        idx_val_missing = []
        idx_val_latest: date | None = None
        for idx in indexes:
            max_d = (
                self._db.query(func.max(IndexValuationModel.trade_date))
                .filter(IndexValuationModel.index_code == idx.index_code)
                .scalar()
            )
            if max_d is None:
                idx_val_missing.append(
                    {
                        "code": idx.index_code,
                        "name": idx.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
            else:
                if idx_val_latest is None or max_d > idx_val_latest:
                    idx_val_latest = max_d
                if max_d < stale_threshold:
                    idx_val_stale.append(
                        {
                            "code": idx.index_code,
                            "name": idx.name_cn,
                            "latest_date": str(max_d),
                            "is_stale": True,
                        }
                    )

        result["index_valuation"] = {
            "total": len(indexes),
            "up_to_date": len(indexes) - len(idx_val_stale) - len(idx_val_missing),
            "stale": idx_val_stale,
            "missing": idx_val_missing,
            "latest_date": str(idx_val_latest) if idx_val_latest else None,
        }

        # --- 字段级质量 ---
        total_etf_bars = self._db.query(func.count(EtfDailyBarModel.id)).scalar() or 0
        null_etf_change_pct = (
            self._db.query(func.count(EtfDailyBarModel.id))
            .filter(EtfDailyBarModel.change_pct.is_(None))
            .scalar()
            or 0
        )
        total_index_bars = self._db.query(func.count(IndexDailyBarModel.id)).scalar() or 0
        null_index_change_pct = (
            self._db.query(func.count(IndexDailyBarModel.id))
            .filter(IndexDailyBarModel.change_pct.is_(None))
            .scalar()
            or 0
        )
        result["field_quality"] = {
            "etf_bars": {
                "total_records": total_etf_bars,
                "change_pct_null": null_etf_change_pct,
                "change_pct_null_rate": round(null_etf_change_pct / total_etf_bars, 4)
                if total_etf_bars
                else 0,
            },
            "index_bars": {
                "total_records": total_index_bars,
                "change_pct_null": null_index_change_pct,
                "change_pct_null_rate": round(null_index_change_pct / total_index_bars, 4)
                if total_index_bars
                else 0,
            },
        }

        result["checked_at"] = datetime.now(timezone.utc).isoformat()
        return result

    # ==================================================================
    # 全量日频摄取（后台线程入口）
    # ==================================================================

    def run_daily_ingest(self, run_id: str) -> None:
        """执行日频数据全量摄取任务（后台线程入口）。

        依次拉取：
        1. 所有活跃 ETF 的日线和份额
        2. 所有基准指数的日线和估值
        3. 宏观指标（CPI/PMI/LPR）

        完成后更新 research_run 状态为 success/failed。
        """
        start_time = datetime.now(timezone.utc)
        try:
            run = self._db.query(ResearchRunModel).filter(ResearchRunModel.run_id == run_id).first()
            if run is None:
                logger.error("run_daily_ingest: run_id %s 不存在", run_id)
                return

            run.status = "running"
            run.started_at = start_time
            self._db.commit()

            today = date.today()

            if today.weekday() >= 5:
                run.status = "success"
                run.finished_at = datetime.now(timezone.utc)
                run.metrics = {"reason": "weekend", "message": "周末休市，跳过数据摄取"}
                self._db.commit()
                return

            # ------------------------------ 1. ETF 行情 + 份额 ------------------------------
            etfs = self._universe_repo.find_all_active()

            etf_success = 0
            etf_failed = 0
            etf_skipped = 0

            if etfs:
                for etf in etfs:
                    etf_code = etf.etf_code
                    item_status = "success"
                    item_message = ""

                    try:
                        latest_bar_date = self._fetch_and_upsert_bars_incremental(etf_code)
                        if not latest_bar_date:
                            item_status = "skipped"
                            item_message = "未获取到 K 线数据"
                            etf_skipped += 1
                        elif latest_bar_date == str(today):
                            try:
                                self._fetch_and_upsert_shares(etf_code, today)
                            except Exception:
                                item_message = "份额数据拉取失败"
                            etf_success += 1
                        else:
                            item_status = "skipped"
                            item_message = f"非交易日，最新行情日期为 {latest_bar_date}"
                            etf_skipped += 1
                    except Exception as e:
                        item_status = "failed"
                        item_message = str(e)[:500]
                        etf_failed += 1
                        self._db.rollback()
                        logger.warning("ETF %s 数据摄取失败: %s", etf_code, e)

                    try:
                        item = ResearchRunItemModel(
                            run_id=run_id,
                            etf_code=etf_code,
                            status=item_status,
                            message=item_message or None,
                        )
                        self._db.add(item)
                        self._db.commit()
                    except Exception:
                        self._db.rollback()
                        logger.warning("写入 ResearchRunItem 失败: %s", etf_code, exc_info=True)

            # ------------------------------ 2. 指数日线 + 估值 ------------------------------
            indexes = (
                self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()
            )
            index_bar_count = 0
            index_valuation_count = 0

            for idx in indexes:
                try:
                    index_bar_count += self._fetch_and_upsert_index_bars(idx.index_code)
                except Exception as e:
                    logger.warning("指数 %s 日线拉取失败: %s", idx.index_code, e)

                try:
                    index_valuation_count += self._fetch_and_upsert_index_valuation(idx.index_code)
                except Exception as e:
                    logger.warning("指数 %s 估值拉取失败: %s", idx.index_code, e)

            # ------------------------------ 3. 宏观指标 ------------------------------
            macro_count = 0
            try:
                macro_count = self._fetch_and_upsert_macro()
            except Exception as e:
                logger.warning("宏观指标拉取失败: %s", e)

            # ------------------------------ 汇总 ------------------------------
            run = self._db.query(ResearchRunModel).filter(ResearchRunModel.run_id == run_id).first()
            if run is None:
                return
            run.status = "success"
            run.finished_at = datetime.now(timezone.utc)
            run.metrics = {
                "etf": {
                    "total": len(etfs),
                    "success": etf_success,
                    "failed": etf_failed,
                    "skipped": etf_skipped,
                },
                "index": {
                    "total": len(indexes),
                    "bar_records": index_bar_count,
                    "valuation_records": index_valuation_count,
                },
                "macro": {"records": macro_count},
                "duration_seconds": round(
                    (datetime.now(timezone.utc) - start_time).total_seconds(), 1
                ),
            }
            self._db.commit()

        except Exception as e:
            self._db.rollback()
            logger.warning("run_daily_ingest 整体失败: %s", e, exc_info=True)
            try:
                run = (
                    self._db.query(ResearchRunModel)
                    .filter(ResearchRunModel.run_id == run_id)
                    .first()
                )
                if run is not None:
                    run.status = "failed"
                    run.finished_at = datetime.now(timezone.utc)
                    run.error_message = str(e)[:1000]
                    self._db.commit()
            except Exception:
                logger.warning("更新失败状态时出错", exc_info=True)
                self._db.rollback()

    def run_cold_start(self, run_id: str) -> None:
        """冷启动：拉取全部 ETF 和指数的全量历史数据（后台线程入口）。

        与 run_daily_ingest 的区别：
        - ETF 日线拉取全量历史（~320 条），而非仅最近 5 条
        - 不拉取份额快照（份额为点状数据，无历史含义）
        - 跳过周末检查（冷启动可随时执行）
        - 指数日线/估值/宏观复用现有全量方法
        """
        start_time = datetime.now(timezone.utc)
        try:
            run = self._db.query(ResearchRunModel).filter(ResearchRunModel.run_id == run_id).first()
            if run is None:
                logger.error("run_cold_start: run_id %s 不存在", run_id)
                return

            run.status = "running"
            run.started_at = start_time
            self._db.commit()

            # 1. 全量 ETF 历史日线
            etfs = self._universe_repo.find_all_active()

            etf_bar_count = 0
            etf_failed_count = 0

            for etf in etfs:
                etf_code = etf.etf_code
                try:
                    n = self._fetch_and_upsert_bars_full_history(etf_code)
                    etf_bar_count += n
                    self._db.add(
                        ResearchRunItemModel(
                            run_id=run_id,
                            etf_code=etf_code,
                            status="success",
                            message=f"写入 {n} 条日线",
                        )
                    )
                except Exception as e:
                    etf_failed_count += 1
                    self._db.rollback()
                    logger.warning("ETF %s 全量日线拉取失败: %s", etf_code, e)
                try:
                    self._db.commit()
                except Exception:
                    self._db.rollback()

            # 2. 指数全量日线 + 估值
            indexes = (
                self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()
            )
            index_bar_count = 0
            index_valuation_count = 0

            for idx in indexes:
                try:
                    index_bar_count += self._fetch_and_upsert_index_bars(idx.index_code)
                except Exception as e:
                    logger.warning("指数 %s 日线拉取失败: %s", idx.index_code, e)

                try:
                    index_valuation_count += self._fetch_and_upsert_index_valuation(idx.index_code)
                except Exception as e:
                    logger.warning("指数 %s 估值拉取失败: %s", idx.index_code, e)

            # 3. 宏观指标
            macro_count = 0
            try:
                macro_count = self._fetch_and_upsert_macro()
            except Exception as e:
                logger.warning("宏观指标拉取失败: %s", e)

            # 汇总
            run = self._db.query(ResearchRunModel).filter(ResearchRunModel.run_id == run_id).first()
            if run is None:
                return
            run.status = "success"
            run.finished_at = datetime.now(timezone.utc)
            run.metrics = {
                "etf": {
                    "total": len(etfs),
                    "bar_records": etf_bar_count,
                    "failed": etf_failed_count,
                },
                "index": {
                    "total": len(indexes),
                    "bar_records": index_bar_count,
                    "valuation_records": index_valuation_count,
                },
                "macro": {"records": macro_count},
                "duration_seconds": round(
                    (datetime.now(timezone.utc) - start_time).total_seconds(), 1
                ),
            }
            self._db.commit()

        except Exception as e:
            self._db.rollback()
            logger.warning("run_cold_start 整体失败: %s", e, exc_info=True)
            try:
                run = (
                    self._db.query(ResearchRunModel)
                    .filter(ResearchRunModel.run_id == run_id)
                    .first()
                )
                if run is not None:
                    run.status = "failed"
                    run.finished_at = datetime.now(timezone.utc)
                    run.error_message = str(e)[:1000]
                    self._db.commit()
            except Exception:
                logger.warning("更新失败状态时出错", exc_info=True)
                self._db.rollback()

    def run_startup_fill(self, run_id: str) -> None:
        """启动补全：检查所有 ETF 和指数的数据缺口并按需补全（系统启动时自动调用）。

        与 run_cold_start 的区别：
        - ETF 日线使用增量方法，已有当日数据的 ETF 直接跳过，避免重复 API 调用
        - 指数/宏观数据仅在超过 5 天未更新时才重新拉取
        """
        start_time = datetime.now(timezone.utc)
        stale_threshold = date.today() - timedelta(days=5)

        try:
            run = self._db.query(ResearchRunModel).filter(ResearchRunModel.run_id == run_id).first()
            if run is None:
                logger.error("run_startup_fill: run_id %s 不存在", run_id)
                return

            run.status = "running"
            run.started_at = start_time
            self._db.commit()

            # 1. ETF 日线增量补全（已是最新则跳过）
            etfs = self._universe_repo.find_all_active()
            etf_filled = 0
            etf_skipped = 0
            etf_failed = 0

            for etf in etfs:
                etf_code = etf.etf_code
                try:
                    latest = self._fetch_and_upsert_bars_incremental(etf_code)
                    if latest == str(date.today()):
                        etf_skipped += 1
                    else:
                        etf_filled += 1
                except Exception as e:
                    etf_failed += 1
                    self._db.rollback()
                    logger.warning("ETF %s 启动补全失败: %s", etf_code, e)

            # 2. 指数日线 + 估值（超过 5 天未更新才重新拉取）
            indexes = (
                self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()
            )
            index_bar_count = 0
            index_val_count = 0

            for idx in indexes:
                idx_max = (
                    self._db.query(func.max(IndexDailyBarModel.trade_date))
                    .filter(IndexDailyBarModel.index_code == idx.index_code)
                    .scalar()
                )
                if idx_max is None or idx_max <= stale_threshold:
                    try:
                        index_bar_count += self._fetch_and_upsert_index_bars(idx.index_code)
                    except Exception as e:
                        logger.warning("指数 %s 日线补全失败: %s", idx.index_code, e)

                    try:
                        index_val_count += self._fetch_and_upsert_index_valuation(idx.index_code)
                    except Exception as e:
                        logger.warning("指数 %s 估值补全失败: %s", idx.index_code, e)

            # 3. 宏观指标（超过 5 天未入库才重新拉取）
            macro_latest_ingest: datetime | None = self._db.query(
                func.max(MacroIndicatorModel.ingested_at)
            ).scalar()
            # DateTime 列返回 naive datetime，去掉 tzinfo 后再比较
            macro_stale_threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                days=5
            )
            macro_count = 0
            if macro_latest_ingest is None or macro_latest_ingest < macro_stale_threshold:
                try:
                    macro_count = self._fetch_and_upsert_macro()
                except Exception as e:
                    logger.warning("宏观指标补全失败: %s", e)

            run = self._db.query(ResearchRunModel).filter(ResearchRunModel.run_id == run_id).first()
            if run is None:
                return
            run.status = "success"
            run.finished_at = datetime.now(timezone.utc)
            run.metrics = {
                "etf": {
                    "total": len(etfs),
                    "filled": etf_filled,
                    "skipped": etf_skipped,
                    "failed": etf_failed,
                },
                "index": {
                    "bar_records": index_bar_count,
                    "valuation_records": index_val_count,
                },
                "macro": {"records": macro_count},
                "duration_seconds": round(
                    (datetime.now(timezone.utc) - start_time).total_seconds(), 1
                ),
            }
            self._db.commit()

        except Exception as e:
            self._db.rollback()
            logger.warning("run_startup_fill 整体失败: %s", e, exc_info=True)
            try:
                run = (
                    self._db.query(ResearchRunModel)
                    .filter(ResearchRunModel.run_id == run_id)
                    .first()
                )
                if run is not None:
                    run.status = "failed"
                    run.finished_at = datetime.now(timezone.utc)
                    run.error_message = str(e)[:1000]
                    self._db.commit()
            except Exception:
                logger.warning("更新失败状态时出错", exc_info=True)
                self._db.rollback()
