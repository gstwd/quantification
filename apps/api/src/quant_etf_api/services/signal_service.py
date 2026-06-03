from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import EtfFactorValueModel, EtfSignalModel
from quant_etf_api.schemas.signal import FactorRow, SignalRow

logger = logging.getLogger(__name__)


class SignalService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def latest_signals(
        self, strategy_id: str, offset: int = 0, limit: int = 50
    ) -> tuple[list[SignalRow], int]:
        """分页查询指定策略最新交易日的所有 ETF 信号。

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
                self._db.query(func.max(EtfSignalModel.trade_date))
                .filter(EtfSignalModel.strategy_id == strategy_id)
                .scalar()
            )
            if max_date is None:
                return [], 0
            base_q = self._db.query(EtfSignalModel).filter(
                EtfSignalModel.strategy_id == strategy_id,
                EtfSignalModel.trade_date == max_date,
            )
            total = base_q.count()
            rows = (
                base_q.order_by(EtfSignalModel.signal_score.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            items = [
                SignalRow(
                    trade_date=r.trade_date,
                    etf_code=r.etf_code,
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
        self, strategy_id: str, etf_code: str, offset: int = 0, limit: int = 50
    ) -> tuple[list[SignalRow], int]:
        """分页查询某策略在某 ETF 上的历史信号。

        Args:
            strategy_id: 策略 ID。
            etf_code: ETF 代码。
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        try:
            base_q = self._db.query(EtfSignalModel).filter(
                EtfSignalModel.strategy_id == strategy_id,
                EtfSignalModel.etf_code == etf_code,
            )
            total = base_q.count()
            rows = (
                base_q.order_by(EtfSignalModel.trade_date.desc()).offset(offset).limit(limit).all()
            )
            items = [
                SignalRow(
                    trade_date=r.trade_date,
                    etf_code=r.etf_code,
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

    def factor_rows(self, etf_code: str, trade_date: date) -> list[FactorRow]:
        try:
            rows = (
                self._db.query(EtfFactorValueModel)
                .filter(
                    EtfFactorValueModel.etf_code == etf_code,
                    EtfFactorValueModel.trade_date == trade_date,
                )
                .all()
            )
            return [
                FactorRow(
                    trade_date=r.trade_date,
                    etf_code=r.etf_code,
                    factor_id=r.factor_id,
                    factor_value_numeric=r.factor_value_numeric,
                    factor_value_text=r.factor_value_text,
                    factor_payload=_parse_json_payload(r.factor_payload),
                    strategy_id=r.strategy_id,
                )
                for r in rows
            ]
        except Exception:
            logger.warning("factor_rows DB query failed", exc_info=True)
            return []


def _parse_json_payload(value: dict | str | None) -> dict:
    """将 JSON 字段值（可能是字符串或字典）统一转为字典。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    import json

    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
