from __future__ import annotations

import logging
import threading
from datetime import date, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.eastmoney import EastmoneyClient
from quant_etf_api.infra.clients.tencent import TencentClient, TencentDailyBar
from quant_etf_api.infra.db.models.core import (
    EtfDailyBarModel,
    EtfDailyShareModel,
    EtfUniverseModel,
    ResearchRunItemModel,
    ResearchRunModel,
)
from quant_etf_api.schemas.market_data import DailyBar, ShareSnapshot

logger = logging.getLogger(__name__)

# 每个 ETF 独立一把锁，防止并发冷启动时多线程重复拉取同一 ETF 的外部 API
_fetch_locks: dict[str, threading.Lock] = {}
_fetch_locks_meta = threading.Lock()  # 保护 _fetch_locks 字典本身的并发写入


def _get_lock(key: str) -> threading.Lock:
    # 双重检查：先不加锁快速判断，再加锁安全创建
    with _fetch_locks_meta:
        if key not in _fetch_locks:
            _fetch_locks[key] = threading.Lock()
        return _fetch_locks[key]


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


class IngestService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def latest_trade_date(self) -> date:
        return date.today()

    def _fetch_and_upsert_bars(self, etf_code: str, limit: int = 5) -> list[TencentDailyBar]:
        """从腾讯拉取 K 线数据并幂等写入 DB，返回原始数据列表。

        失败时抛出异常，由调用方决定如何处理。
        """
        bars = TencentClient().fetch_daily_bars(etf_code, limit)
        if not bars:
            return []
        stmt = insert(EtfDailyBarModel).values(
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
        ).on_conflict_do_nothing(constraint="uq_etf_daily_bar")
        self._db.execute(stmt)
        self._db.commit()
        return bars

    def get_daily_bars(self, etf_code: str, limit: int = 30) -> list[DailyBar]:
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

                self._fetch_and_upsert_bars(etf_code, limit)
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
            logger.warning("get_daily_bars failed for %s, returning stub", etf_code, exc_info=True)
            self._db.rollback()

        today = date.today()
        return [
            DailyBar(
                trade_date=today,
                code=etf_code,
                open_price=4.0,
                high_price=4.1,
                low_price=3.95,
                close_price=4.05,
                change_pct=0.62,
                volume=123456.0,
                turnover=456789000.0,
                source="stub",
                ingested_at=datetime.utcnow(),
            )
        ]

    def _fetch_and_upsert_shares(self, etf_code: str, trade_date: date) -> None:
        """从东方财富拉取份额快照并幂等写入 DB。

        失败时抛出异常，由调用方决定如何处理。
        """
        etf_row = self._db.get(EtfUniverseModel, etf_code)
        exchange = etf_row.exchange if etf_row else None
        snapshot = EastmoneyClient().fetch_share_snapshot(etf_code, exchange=exchange)
        if snapshot is None:
            raise ValueError(f"无法获取 {etf_code} 的份额数据")
        stmt = insert(EtfDailyShareModel).values(
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
        ).on_conflict_do_nothing(constraint="uq_etf_daily_share")
        self._db.execute(stmt)
        self._db.commit()

    def get_share_history(self, etf_code: str, limit: int = 30) -> list[ShareSnapshot]:
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
            logger.warning("get_share_history failed for %s, returning stub", etf_code, exc_info=True)
            self._db.rollback()

        today = date.today()
        return [
            ShareSnapshot(
                trade_date=today,
                etf_code=etf_code,
                shares_total=380.2,
                shares_delta=5.1,
                shares_delta_pct=1.36,
                nav=4.02,
                aum=1528.0,
                source="stub",
                ingested_at=datetime.utcnow(),
            )
        ]

    def run_daily_ingest(self, run_id: str) -> None:
        """执行日频数据全量摄取任务（后台线程入口）。

        遍历所有活跃 ETF，拉取 K 线和份额数据并入库，
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

            # 周末跳过，避免无效的外部 API 调用
            if today.weekday() >= 5:
                run.status = "success"
                run.finished_at = datetime.utcnow()
                run.metrics = {"reason": "weekend", "message": "周末休市，跳过数据摄取"}
                self._db.commit()
                return

            etfs = (
                self._db.query(EtfUniverseModel)
                .filter(EtfUniverseModel.is_active.is_(True))
                .order_by(EtfUniverseModel.etf_code)
                .all()
            )

            if not etfs:
                run.status = "success"
                run.finished_at = datetime.utcnow()
                run.metrics = {"total": 0, "message": "无活跃 ETF"}
                self._db.commit()
                return

            success_count = 0
            failed_count = 0
            skipped_count = 0

            for etf in etfs:
                etf_code = etf.etf_code
                item_status = "success"
                item_message = ""

                try:
                    bars = self._fetch_and_upsert_bars(etf_code, limit=5)

                    # 判断是否为交易日：拉取的最新 K 线日期是否等于今天
                    if bars:
                        latest_bar_date = bars[-1].trade_date
                        if latest_bar_date != str(today):
                            item_status = "skipped"
                            item_message = f"非交易日，最新行情日期为 {latest_bar_date}"
                            skipped_count += 1
                        else:
                            try:
                                self._fetch_and_upsert_shares(etf_code, today)
                            except Exception:
                                item_message = "份额数据拉取失败"
                            success_count += 1
                    else:
                        item_status = "skipped"
                        item_message = "未获取到 K 线数据"
                        skipped_count += 1
                except Exception as e:
                    item_status = "failed"
                    item_message = str(e)[:500]
                    failed_count += 1
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

            run = self._db.query(ResearchRunModel).filter(ResearchRunModel.run_id == run_id).first()
            if run is None:
                return
            run.status = "success"
            run.finished_at = datetime.utcnow()
            run.metrics = {
                "total": len(etfs),
                "success": success_count,
                "failed": failed_count,
                "skipped": skipped_count,
                "duration_seconds": round((datetime.utcnow() - start_time).total_seconds(), 1),
            }
            self._db.commit()

        except Exception as e:
            self._db.rollback()
            logger.warning("run_daily_ingest 整体失败: %s", e, exc_info=True)
            try:
                run = self._db.query(ResearchRunModel).filter(ResearchRunModel.run_id == run_id).first()
                if run is not None:
                    run.status = "failed"
                    run.finished_at = datetime.utcnow()
                    run.error_message = str(e)[:1000]
                    self._db.commit()
            except Exception:
                logger.warning("更新失败状态时出错", exc_info=True)
                self._db.rollback()
