"""ETF 份额仓库。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func

from quant_etf_api.infra.db.models.core import EtfDailyShareModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class EtfDailyShareRepository(BaseRepository):
    """EtfDailyShareModel 的只读查询仓库。"""

    def find_by_code_limit(self, code: str, limit: int) -> list[EtfDailyShareModel]:
        """按 ETF 代码查询最近 N 条份额记录（升序）。"""
        rows = (
            self._db.query(EtfDailyShareModel)
            .filter(EtfDailyShareModel.etf_code == code)
            .order_by(EtfDailyShareModel.trade_date.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

    def find_by_codes_date(
        self, codes: list[str], trade_date: date
    ) -> dict[str, EtfDailyShareModel]:
        """按代码列表和日期查询份额快照，返回 code → 行 的映射。"""
        rows = (
            self._db.query(EtfDailyShareModel)
            .filter(
                EtfDailyShareModel.etf_code.in_(codes),
                EtfDailyShareModel.trade_date == trade_date,
            )
            .all()
        )
        return {r.etf_code: r for r in rows}

    def find_by_codes_date_range(
        self, codes: list[str], start: date, end: date
    ) -> dict[tuple[str, date], EtfDailyShareModel]:
        """批量按代码列表和日期范围查询，返回 (code, date) → 行 的映射。"""
        rows = (
            self._db.query(EtfDailyShareModel)
            .filter(
                EtfDailyShareModel.etf_code.in_(codes),
                EtfDailyShareModel.trade_date >= start,
                EtfDailyShareModel.trade_date <= end,
            )
            .all()
        )
        return {(r.etf_code, r.trade_date): r for r in rows}

    def get_latest_date(self, code: str) -> date | None:
        """获取某 ETF 最新份额数据的日期。"""
        return (
            self._db.query(func.max(EtfDailyShareModel.trade_date))
            .filter(EtfDailyShareModel.etf_code == code)
            .scalar()
        )
