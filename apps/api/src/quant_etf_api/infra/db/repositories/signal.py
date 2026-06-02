"""信号与因子值仓库。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func

from quant_etf_api.infra.db.models.core import EtfFactorValueModel, EtfSignalModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class SignalRepository(BaseRepository):
    """信号与因子值的只读查询仓库。"""

    def find_latest_by_strategy(self, strategy_id: str) -> list[EtfSignalModel]:
        """查询某策略在各 ETF 上的最新信号（子查询去重）。"""
        subq = (
            self._db.query(
                EtfSignalModel.etf_code,
                func.max(EtfSignalModel.trade_date).label("max_date"),
            )
            .filter(EtfSignalModel.strategy_id == strategy_id)
            .group_by(EtfSignalModel.etf_code)
            .subquery()
        )
        rows = (
            self._db.query(EtfSignalModel)
            .join(
                subq,
                and_(
                    EtfSignalModel.etf_code == subq.c.etf_code,
                    EtfSignalModel.trade_date == subq.c.max_date,
                    EtfSignalModel.strategy_id == strategy_id,
                ),
            )
            .order_by(EtfSignalModel.etf_code.asc())
            .all()
        )
        return rows

    def find_signals(
        self,
        strategy_id: str,
        etf_code: str,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[EtfSignalModel], int]:
        """分页查询某策略在某 ETF 上的历史信号。"""
        base_q = self._db.query(EtfSignalModel).filter(
            EtfSignalModel.strategy_id == strategy_id,
            EtfSignalModel.etf_code == etf_code,
        )
        total = base_q.count()
        rows = base_q.order_by(EtfSignalModel.trade_date.desc()).offset(offset).limit(limit).all()
        return rows, total

    def find_factors_by_etf_date(
        self, etf_code: str, trade_date: date
    ) -> list[EtfFactorValueModel]:
        """查询某 ETF 在某日的所有因子值。"""
        return (
            self._db.query(EtfFactorValueModel)
            .filter(
                EtfFactorValueModel.etf_code == etf_code,
                EtfFactorValueModel.trade_date == trade_date,
            )
            .all()
        )
