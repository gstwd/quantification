from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import IndexSignalModel
from quant_etf_api.schemas.signal import SignalRow

logger = logging.getLogger(__name__)


class SignalService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def latest_signals(
        self, strategy_id: str, offset: int = 0, limit: int = 50
    ) -> tuple[list[SignalRow], int]:
        """分页查询指定策略最新交易日的所有指数信号。

        Args:
            strategy_id: 策略 ID。
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        try:
            from sqlalchemy import func

            max_date = (
                self._db.query(func.max(IndexSignalModel.trade_date))
                .filter(IndexSignalModel.strategy_id == strategy_id)
                .scalar()
            )
            if max_date is None:
                return [], 0
            base_q = self._db.query(IndexSignalModel).filter(
                IndexSignalModel.strategy_id == strategy_id,
                IndexSignalModel.trade_date == max_date,
            )
            total = base_q.count()
            rows = (
                base_q.order_by(IndexSignalModel.signal_score.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            items = [
                SignalRow(
                    trade_date=r.trade_date,
                    index_code=r.index_code,
                    strategy_id=r.strategy_id,
                    signal_score=r.signal_score,
                    signal_level=r.signal_level,
                    signal_label=r.signal_label,
                    signal_payload=r.signal_payload or {},
                )
                for r in rows
            ]
            return items, total
        except Exception:
            logger.warning("latest_signals DB query failed for %s", strategy_id, exc_info=True)
            return [], 0

    def signal_history(
        self, strategy_id: str, index_code: str, offset: int = 0, limit: int = 50
    ) -> tuple[list[SignalRow], int]:
        """分页查询某策略在某指数上的历史信号。

        Args:
            strategy_id: 策略 ID。
            index_code: 指数代码。
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        try:
            base_q = self._db.query(IndexSignalModel).filter(
                IndexSignalModel.strategy_id == strategy_id,
                IndexSignalModel.index_code == index_code,
            )
            total = base_q.count()
            rows = (
                base_q.order_by(IndexSignalModel.trade_date.desc()).offset(offset).limit(limit).all()
            )
            items = [
                SignalRow(
                    trade_date=r.trade_date,
                    index_code=r.index_code,
                    strategy_id=r.strategy_id,
                    signal_score=r.signal_score,
                    signal_level=r.signal_level,
                    signal_label=r.signal_label,
                    signal_payload=r.signal_payload or {},
                )
                for r in rows
            ]
            return items, total
        except Exception:
            logger.warning("signal_history DB query failed", exc_info=True)
            return [], 0
