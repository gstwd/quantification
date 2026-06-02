"""ETF 日线行情仓库。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func

from quant_etf_api.infra.db.models.core import EtfDailyBarModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class EtfDailyBarRepository(BaseRepository):
    """EtfDailyBarModel 的只读查询仓库。"""

    def find_by_code_date_range(self, code: str, start: date, end: date) -> list[EtfDailyBarModel]:
        """按 ETF 代码和日期范围查询日线（升序）。"""
        return (
            self._db.query(EtfDailyBarModel)
            .filter(
                and_(
                    EtfDailyBarModel.etf_code == code,
                    EtfDailyBarModel.trade_date >= start,
                    EtfDailyBarModel.trade_date <= end,
                )
            )
            .order_by(EtfDailyBarModel.trade_date.asc())
            .all()
        )

    def find_by_code_limit(self, code: str, limit: int) -> list[EtfDailyBarModel]:
        """按 ETF 代码查询最近 N 条日线（升序）。"""
        rows = (
            self._db.query(EtfDailyBarModel)
            .filter(EtfDailyBarModel.etf_code == code)
            .order_by(EtfDailyBarModel.trade_date.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

    def find_by_codes_date_range(
        self, codes: list[str], start: date, end: date
    ) -> dict[tuple[str, date], EtfDailyBarModel]:
        """批量按代码列表和日期范围查询，返回 (code, date) → 行 的映射。"""
        rows = (
            self._db.query(EtfDailyBarModel)
            .filter(
                and_(
                    EtfDailyBarModel.etf_code.in_(codes),
                    EtfDailyBarModel.trade_date >= start,
                    EtfDailyBarModel.trade_date <= end,
                )
            )
            .all()
        )
        return {(r.etf_code, r.trade_date): r for r in rows}

    def get_latest_date(self, code: str) -> date | None:
        """获取某 ETF 的最新日线日期。"""
        return (
            self._db.query(func.max(EtfDailyBarModel.trade_date))
            .filter(EtfDailyBarModel.etf_code == code)
            .scalar()
        )

    def get_date_range(self, code: str) -> tuple[date | None, date | None]:
        """获取某 ETF 日线的最早和最晚日期。"""
        row = (
            self._db.query(
                func.min(EtfDailyBarModel.trade_date),
                func.max(EtfDailyBarModel.trade_date),
            )
            .filter(EtfDailyBarModel.etf_code == code)
            .one()
        )
        return row[0], row[1]

    def get_trading_dates(self, codes: list[str], start: date, end: date) -> list[date]:
        """从日线中提取区间内的交易日列表（去重、升序）。"""
        rows = (
            self._db.query(EtfDailyBarModel.trade_date)
            .filter(
                and_(
                    EtfDailyBarModel.trade_date >= start,
                    EtfDailyBarModel.trade_date <= end,
                    EtfDailyBarModel.etf_code.in_(codes),
                )
            )
            .distinct()
            .order_by(EtfDailyBarModel.trade_date.asc())
            .all()
        )
        return [r.trade_date for r in rows]
