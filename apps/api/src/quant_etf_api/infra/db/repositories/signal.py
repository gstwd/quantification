"""信号仓库（已废弃，由 SignalService 替代）。"""

from __future__ import annotations

from sqlalchemy import and_, func

from quant_etf_api.infra.db.models.core import IndexSignalModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class SignalRepository(BaseRepository):
    """信号的只读查询仓库（已废弃，由 SignalService 替代）。"""

    def find_latest_by_strategy(self, strategy_id: str) -> list[IndexSignalModel]:
        """查询某策略在各指数上的最新信号（子查询去重）。"""
        subq = (
            self._db.query(
                IndexSignalModel.index_code,
                func.max(IndexSignalModel.trade_date).label("max_date"),
            )
            .filter(IndexSignalModel.strategy_id == strategy_id)
            .group_by(IndexSignalModel.index_code)
            .subquery()
        )
        rows = (
            self._db.query(IndexSignalModel)
            .join(
                subq,
                and_(
                    IndexSignalModel.index_code == subq.c.index_code,
                    IndexSignalModel.trade_date == subq.c.max_date,
                    IndexSignalModel.strategy_id == strategy_id,
                ),
            )
            .order_by(IndexSignalModel.index_code.asc())
            .all()
        )
        return rows

    def find_signals(
        self,
        strategy_id: str,
        index_code: str,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[IndexSignalModel], int]:
        """分页查询某策略在某指数上的历史信号。"""
        base_q = self._db.query(IndexSignalModel).filter(
            IndexSignalModel.strategy_id == strategy_id,
            IndexSignalModel.index_code == index_code,
        )
        total = base_q.count()
        rows = base_q.order_by(IndexSignalModel.trade_date.desc()).offset(offset).limit(limit).all()
        return rows, total
