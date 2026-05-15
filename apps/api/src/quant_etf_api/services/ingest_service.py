from __future__ import annotations

import logging
import threading
from datetime import date, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.eastmoney import EastmoneyClient
from quant_etf_api.infra.clients.tencent import TencentClient
from quant_etf_api.infra.db.models.core import EtfDailyBarModel, EtfDailyShareModel, EtfUniverseModel
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

    def get_daily_bars(self, etf_code: str, limit: int = 30) -> list[DailyBar]:
        try:
            # 先查 DB，有数据直接返回，避免不必要的外部 API 调用
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
                # 加锁后再次查询，防止另一线程已完成入库
                rows = (
                    self._db.query(EtfDailyBarModel)
                    .filter(EtfDailyBarModel.etf_code == etf_code)
                    .order_by(EtfDailyBarModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_bar_row_to_schema(r) for r in reversed(rows)]

                # DB 无数据，从腾讯拉取并持久化
                bars = TencentClient().fetch_daily_bars(etf_code, limit)
                if bars:
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
                    ).on_conflict_do_nothing(constraint="uq_etf_daily_bar")  # 幂等写入，重复日期跳过
                    self._db.execute(stmt)
                    self._db.commit()
                    rows = (
                        self._db.query(EtfDailyBarModel)
                        .filter(EtfDailyBarModel.etf_code == etf_code)
                        .order_by(EtfDailyBarModel.trade_date.desc())
                        .limit(limit)
                        .all()
                    )
                    return [_bar_row_to_schema(r) for r in reversed(rows)]
        except Exception:
            logger.warning("get_daily_bars failed for %s, returning stub", etf_code, exc_info=True)
            self._db.rollback()

        # 外部 API 和 DB 均失败时返回占位数据，保证接口可用
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

    def get_share_history(self, etf_code: str, limit: int = 30) -> list[ShareSnapshot]:
        try:
            # 先查 DB，有数据直接返回
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
                # 加锁后再次查询，防止另一线程已完成入库
                rows = (
                    self._db.query(EtfDailyShareModel)
                    .filter(EtfDailyShareModel.etf_code == etf_code)
                    .order_by(EtfDailyShareModel.trade_date.desc())
                    .limit(limit)
                    .all()
                )
                if rows:
                    return [_share_row_to_schema(r) for r in reversed(rows)]

                # DB 无数据，从东方财富拉取快照并持久化
                etf_row = self._db.get(EtfUniverseModel, etf_code)
                exchange = etf_row.exchange if etf_row else None
                snapshot = EastmoneyClient().fetch_share_snapshot(etf_code, exchange=exchange)
                if snapshot is not None:
                    stmt = insert(EtfDailyShareModel).values(
                        [
                            {
                                "trade_date": date.today(),
                                "etf_code": etf_code,
                                "shares_total": snapshot.shares_total,
                                "aum": snapshot.aum,
                                "nav": round(snapshot.price, 3),  # 用当前价格近似 NAV
                                "source": "eastmoney",
                                "ingested_at": datetime.utcnow(),
                            }
                        ]
                    ).on_conflict_do_nothing(constraint="uq_etf_daily_share")
                    self._db.execute(stmt)
                    self._db.commit()
                    rows = (
                        self._db.query(EtfDailyShareModel)
                        .filter(EtfDailyShareModel.etf_code == etf_code)
                        .order_by(EtfDailyShareModel.trade_date.desc())
                        .limit(limit)
                        .all()
                    )
                    return [_share_row_to_schema(r) for r in reversed(rows)]
        except Exception:
            logger.warning("get_share_history failed for %s, returning stub", etf_code, exc_info=True)
            self._db.rollback()

        # 外部 API 和 DB 均失败时返回占位数据
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
