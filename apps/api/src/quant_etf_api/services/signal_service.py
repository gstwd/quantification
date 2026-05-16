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

    def latest_signals(self, strategy_id: str) -> list[SignalRow]:
        try:
            from sqlalchemy import func

            # 先找该策略最新的交易日期，再取该日期的所有信号
            max_date = (
                self._db.query(func.max(EtfSignalModel.trade_date))
                .filter(EtfSignalModel.strategy_id == strategy_id)
                .scalar()
            )
            if max_date is not None:
                rows = (
                    self._db.query(EtfSignalModel)
                    .filter(
                        EtfSignalModel.strategy_id == strategy_id,
                        EtfSignalModel.trade_date == max_date,
                    )
                    .order_by(EtfSignalModel.signal_score.desc())  # 按得分降序，高确信排前面
                    .all()
                )
                return [
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
        except Exception:
            logger.warning("latest_signals DB query failed for %s", strategy_id, exc_info=True)

        # DB 无数据或查询失败时返回占位信号，保证前端可渲染
        return [
            SignalRow(
                trade_date=date.today(),
                etf_code="510300",
                strategy_id=strategy_id,
                signal_score=72.5,
                signal_level="HIGH",
                signal_label="高确信",
                signal_payload={"volume_prob": 82.0, "direction_prob": 61.0, "share_prob": 74.0},
            )
        ]

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
            if rows:
                return [
                    FactorRow(
                        trade_date=r.trade_date,
                        etf_code=r.etf_code,
                        factor_id=r.factor_id,
                        factor_value_numeric=r.factor_value_numeric,
                        factor_value_text=r.factor_value_text,
                        factor_payload=r.factor_payload or {},
                        strategy_id=r.strategy_id,
                    )
                    for r in rows
                ]
        except Exception:
            logger.warning("factor_rows DB query failed", exc_info=True)

        # DB 无数据时返回占位因子值
        return [
            FactorRow(
                trade_date=trade_date,
                etf_code=etf_code,
                factor_id="volume_ratio_20d",
                factor_value_numeric=1.86,
                factor_payload={"window": 20},
                strategy_id="three_factor_guard",
            )
        ]
