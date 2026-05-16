from __future__ import annotations

import logging
import threading
from datetime import date, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.clients.akshare_macro import AkShareMacroClient
from quant_etf_api.infra.clients.eastmoney import EastmoneyClient
from quant_etf_api.infra.clients.tencent import TencentClient, TencentDailyBar
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    EtfDailyBarModel,
    EtfDailyShareModel,
    EtfUniverseModel,
    IndexDailyBarModel,
    IndexValuationModel,
    MacroIndicatorModel,
    ResearchRunItemModel,
    ResearchRunModel,
)
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

    def __init__(self, db: Session) -> None:
        self._db = db

    def latest_trade_date(self) -> date:
        return date.today()

    # ==================================================================
    # ETF 日线
    # ==================================================================

    def _fetch_and_upsert_bars(self, etf_code: str, limit: int = 5) -> list[TencentDailyBar]:
        """从腾讯拉取 ETF K 线数据并幂等写入 DB。"""
        bars = TencentClient().fetch_daily_bars(etf_code, limit)
        if not bars:
            return []
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
                        "source": "tencent",
                        "ingested_at": datetime.utcnow(),
                    }
                    for b in bars
                ]
            )
            .on_conflict_do_nothing(constraint="uq_etf_daily_bar")
        )
        self._db.execute(stmt)
        self._db.commit()
        return bars

    def _fetch_and_upsert_bars_full_history(self, etf_code: str) -> int:
        """冷启动：拉取 ETF 全量历史日线（~320 条，覆盖约 250 个交易日）并幂等写入。

        Returns:
            写入记录数
        """
        bars = TencentClient().fetch_daily_bars(etf_code, limit=320)
        if not bars:
            return 0
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
                        "source": "tencent",
                        "ingested_at": datetime.utcnow(),
                    }
                    for b in bars
                ]
            )
            .on_conflict_do_nothing(constraint="uq_etf_daily_bar")
        )
        self._db.execute(stmt)
        self._db.commit()
        return len(bars)

    def get_daily_bars(self, etf_code: str, limit: int = 250) -> list[DailyBar]:
        """ETF 日线读穿透缓存。"""
        try:
            rows = (
                self._db.query(EtfDailyBarModel)
                .filter(EtfDailyBarModel.etf_code == etf_code)
                .order_by(EtfDailyBarModel.trade_date.desc())
                .limit(limit)
                .all()
            )
            if rows:
                return [_bar_row_to_schema(r) for r in reversed(rows)]

            with _get_lock(f"bars:{etf_code}"):
                rows = (
                    self._db.query(EtfDailyBarModel)
                    .filter(EtfDailyBarModel.etf_code == etf_code)
                    .order_by(EtfDailyBarModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_bar_row_to_schema(r) for r in reversed(rows)]

                self._fetch_and_upsert_bars_full_history(etf_code)
                rows = (
                    self._db.query(EtfDailyBarModel)
                    .filter(EtfDailyBarModel.etf_code == etf_code)
                    .order_by(EtfDailyBarModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_bar_row_to_schema(r) for r in reversed(rows)]
        except Exception:
            logger.warning("get_daily_bars failed for %s", etf_code, exc_info=True)
            self._db.rollback()
            return []

    # ==================================================================
    # ETF 份额
    # ==================================================================

    def _fetch_and_upsert_shares(self, etf_code: str, trade_date: date) -> None:
        """从东方财富拉取 ETF 份额快照并幂等写入 DB。

        写入后自动计算与前一日份额的 delta 和 delta_pct。
        """
        etf_row = self._db.get(EtfUniverseModel, etf_code)
        exchange = etf_row.exchange if etf_row else None
        snapshot = EastmoneyClient().fetch_share_snapshot(etf_code, exchange=exchange)
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
                        "source": "eastmoney",
                        "ingested_at": datetime.utcnow(),
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

    def get_share_history(self, etf_code: str, limit: int = 30) -> list[ShareSnapshot]:
        """ETF 份额读穿透缓存。"""
        try:
            rows = (
                self._db.query(EtfDailyShareModel)
                .filter(EtfDailyShareModel.etf_code == etf_code)
                .order_by(EtfDailyShareModel.trade_date.desc())
                .limit(limit)
                .all()
            )
            if rows:
                return [_share_row_to_schema(r) for r in reversed(rows)]

            with _get_lock(f"shares:{etf_code}"):
                rows = (
                    self._db.query(EtfDailyShareModel)
                    .filter(EtfDailyShareModel.etf_code == etf_code)
                    .order_by(EtfDailyShareModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_share_row_to_schema(r) for r in reversed(rows)]

                self._fetch_and_upsert_shares(etf_code, date.today())
                rows = (
                    self._db.query(EtfDailyShareModel)
                    .filter(EtfDailyShareModel.etf_code == etf_code)
                    .order_by(EtfDailyShareModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_share_row_to_schema(r) for r in reversed(rows)]
        except Exception:
            logger.warning("get_share_history failed for %s", etf_code, exc_info=True)
            self._db.rollback()
            return []

    # ==================================================================
    # 指数日线（AkShare）
    # ==================================================================

    def _fetch_and_upsert_index_bars(self, index_code: str) -> int:
        """从 AkShare 拉取指数日线并幂等写入 index_daily_bar。

        Returns:
            写入记录数
        """
        bars = AkShareIndexClient().fetch_index_daily(index_code)
        if not bars:
            return 0
        stmt = (
            insert(IndexDailyBarModel)
            .values(
                [
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
                        "ingested_at": datetime.utcnow(),
                    }
                    for b in bars
                ]
            )
            .on_conflict_do_nothing(constraint="uq_index_daily_bar")
        )
        self._db.execute(stmt)
        self._db.commit()
        return len(bars)

    def get_benchmark_indexes(self) -> list[BenchmarkIndex]:
        """返回所有基准指数（从种子表读取）。"""
        rows = (
            self._db.query(BenchmarkIndexModel)
            .order_by(BenchmarkIndexModel.index_code)
            .all()
        )
        return [
            BenchmarkIndex(index_code=r.index_code, index_name=r.name_cn)
            for r in rows
        ]

    def get_index_daily_bars(self, index_code: str, limit: int = 250) -> list[DailyBar]:
        """指数日线读穿透缓存。"""
        try:
            rows = (
                self._db.query(IndexDailyBarModel)
                .filter(IndexDailyBarModel.index_code == index_code)
                .order_by(IndexDailyBarModel.trade_date.desc())
                .limit(limit)
                .all()
            )
            if rows:
                return [_index_bar_row_to_schema(r) for r in reversed(rows)]

            with _get_lock(f"index_bars:{index_code}"):
                rows = (
                    self._db.query(IndexDailyBarModel)
                    .filter(IndexDailyBarModel.index_code == index_code)
                    .order_by(IndexDailyBarModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_index_bar_row_to_schema(r) for r in reversed(rows)]

                self._fetch_and_upsert_index_bars(index_code)
                rows = (
                    self._db.query(IndexDailyBarModel)
                    .filter(IndexDailyBarModel.index_code == index_code)
                    .order_by(IndexDailyBarModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_index_bar_row_to_schema(r) for r in reversed(rows)]
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

        Returns:
            写入记录数
        """
        valuations = AkShareIndexClient().fetch_index_valuation(index_code)
        if not valuations:
            return 0
        stmt = (
            insert(IndexValuationModel)
            .values(
                [
                    {
                        "trade_date": v.trade_date,
                        "index_code": index_code,
                        "pe": v.pe,
                        "pe_percentile": v.pe_percentile,
                        "pb": v.pb,
                        "pb_percentile": v.pb_percentile,
                        "dividend_yield": v.dividend_yield,
                        "source": "akshare",
                        "ingested_at": datetime.utcnow(),
                    }
                    for v in valuations
                ]
            )
            .on_conflict_do_nothing(constraint="uq_index_valuation")
        )
        self._db.execute(stmt)
        self._db.commit()
        return len(valuations)

    def get_index_valuation(self, index_code: str, limit: int = 30) -> list[IndexValuation]:
        """指数估值读穿透缓存。"""
        try:
            rows = (
                self._db.query(IndexValuationModel)
                .filter(IndexValuationModel.index_code == index_code)
                .order_by(IndexValuationModel.trade_date.desc())
                .limit(limit)
                .all()
            )
            if rows:
                return [_index_valuation_row_to_schema(r) for r in reversed(rows)]

            with _get_lock(f"index_valuation:{index_code}"):
                rows = (
                    self._db.query(IndexValuationModel)
                    .filter(IndexValuationModel.index_code == index_code)
                    .order_by(IndexValuationModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_index_valuation_row_to_schema(r) for r in reversed(rows)]

                self._fetch_and_upsert_index_valuation(index_code)
                rows = (
                    self._db.query(IndexValuationModel)
                    .filter(IndexValuationModel.index_code == index_code)
                    .order_by(IndexValuationModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_index_valuation_row_to_schema(r) for r in reversed(rows)]
        except Exception:
            logger.warning(
                "get_index_valuation failed for %s, returning []", index_code, exc_info=True
            )
            self._db.rollback()

        return []

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
                        "ingested_at": datetime.utcnow(),
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
        start_time = datetime.utcnow()
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
                run.finished_at = datetime.utcnow()
                run.metrics = {"reason": "weekend", "message": "周末休市，跳过数据摄取"}
                self._db.commit()
                return

            # ------------------------------ 1. ETF 行情 + 份额 ------------------------------
            etfs = (
                self._db.query(EtfUniverseModel)
                .filter(EtfUniverseModel.is_active.is_(True))
                .order_by(EtfUniverseModel.etf_code)
                .all()
            )

            etf_success = 0
            etf_failed = 0
            etf_skipped = 0

            if etfs:
                for etf in etfs:
                    etf_code = etf.etf_code
                    item_status = "success"
                    item_message = ""

                    try:
                        bars = self._fetch_and_upsert_bars(etf_code, limit=5)
                        if bars:
                            latest_bar_date = bars[-1].trade_date
                            if latest_bar_date != str(today):
                                item_status = "skipped"
                                item_message = f"非交易日，最新行情日期为 {latest_bar_date}"
                                etf_skipped += 1
                            else:
                                try:
                                    self._fetch_and_upsert_shares(etf_code, today)
                                except Exception:
                                    item_message = "份额数据拉取失败"
                                etf_success += 1
                        else:
                            item_status = "skipped"
                            item_message = "未获取到 K 线数据"
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
            run.finished_at = datetime.utcnow()
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
                "duration_seconds": round((datetime.utcnow() - start_time).total_seconds(), 1),
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
                    run.finished_at = datetime.utcnow()
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
        start_time = datetime.utcnow()
        try:
            run = self._db.query(ResearchRunModel).filter(ResearchRunModel.run_id == run_id).first()
            if run is None:
                logger.error("run_cold_start: run_id %s 不存在", run_id)
                return

            run.status = "running"
            run.started_at = start_time
            self._db.commit()

            # 1. 全量 ETF 历史日线
            etfs = (
                self._db.query(EtfUniverseModel)
                .filter(EtfUniverseModel.is_active.is_(True))
                .order_by(EtfUniverseModel.etf_code)
                .all()
            )

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
                    index_valuation_count += self._fetch_and_upsert_index_valuation(
                        idx.index_code
                    )
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
            run.finished_at = datetime.utcnow()
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
                "duration_seconds": round((datetime.utcnow() - start_time).total_seconds(), 1),
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
                    run.finished_at = datetime.utcnow()
                    run.error_message = str(e)[:1000]
                    self._db.commit()
            except Exception:
                logger.warning("更新失败状态时出错", exc_info=True)
                self._db.rollback()
