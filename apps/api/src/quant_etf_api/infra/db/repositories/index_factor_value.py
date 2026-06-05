"""指数因子值仓库，提供指数级因子值的查询方法。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    IndexDailyBarModel,
    IndexFactorValueModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository


class IndexFactorValueRepository(BaseRepository):
    """IndexFactorValueModel 的查询仓库。"""

    def __init__(self, db: Session) -> None:
        """初始化指数因子值仓库。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        super().__init__(db)

    def find_latest_date(self, factor_id: str) -> date | None:
        """查询指定因子在 index_factor_value 中的最大 trade_date。

        Args:
            factor_id: 因子标识。

        Returns:
            最新交易日，无数据时返回 None。
        """
        result = (
            self._db.query(func.max(IndexFactorValueModel.trade_date))
            .filter(IndexFactorValueModel.factor_id == factor_id)
            .scalar()
        )
        return result

    def find_latest_bar_date(self) -> date | None:
        """查询 index_daily_bar 中的最大 trade_date。

        Returns:
            最新行情日期，无数据时返回 None。
        """
        result = self._db.query(func.max(IndexDailyBarModel.trade_date)).scalar()
        return result

    def find_cross_section(
        self, factor_id: str, trade_date: date
    ) -> list[tuple[str, str, float | None, str | None]]:
        """查询指定因子在指定交易日的横截面数据，JOIN 指数中文名。

        Args:
            factor_id: 因子标识。
            trade_date: 交易日。

        Returns:
            (index_code, name_cn, factor_value_numeric, factor_value_text) 元组列表。
        """
        rows = (
            self._db.query(
                IndexFactorValueModel.index_code,
                BenchmarkIndexModel.name_cn,
                IndexFactorValueModel.factor_value_numeric,
                IndexFactorValueModel.factor_value_text,
            )
            .join(
                BenchmarkIndexModel,
                IndexFactorValueModel.index_code == BenchmarkIndexModel.index_code,
            )
            .filter(
                IndexFactorValueModel.factor_id == factor_id,
                IndexFactorValueModel.trade_date == trade_date,
                IndexFactorValueModel.strategy_id.is_(None),
            )
            .order_by(IndexFactorValueModel.index_code)
            .all()
        )
        return rows

    def find_factor_values(
        self,
        factor_id: str,
        index_code: str,
        start_date: date,
        end_date: date,
    ) -> list[IndexFactorValueModel]:
        """查询单因子在单指数上的时间序列。

        Args:
            factor_id: 因子标识。
            index_code: 指数代码。
            start_date: 开始日期（含）。
            end_date: 截止日期（含）。

        Returns:
            按 trade_date 升序排列的 IndexFactorValueModel 列表。
        """
        return (
            self._db.query(IndexFactorValueModel)
            .filter(
                IndexFactorValueModel.factor_id == factor_id,
                IndexFactorValueModel.index_code == index_code,
                IndexFactorValueModel.trade_date >= start_date,
                IndexFactorValueModel.trade_date <= end_date,
                IndexFactorValueModel.strategy_id.is_(None),
            )
            .order_by(IndexFactorValueModel.trade_date.asc())
            .all()
        )

    def find_missing_dates(
        self,
        factor_id: str,
        index_code: str,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        """查询指定指数在 [start, end] 范围内有行情数据但缺失因子值的日期。

        Args:
            factor_id: 因子标识。
            index_code: 指数代码。
            start_date: 开始日期（含）。
            end_date: 截止日期（含）。

        Returns:
            缺失因子值的交易日列表，按日期升序排列。
        """
        bar_dates = {
            r[0]
            for r in self._db.query(IndexDailyBarModel.trade_date)
            .filter(
                IndexDailyBarModel.index_code == index_code,
                IndexDailyBarModel.trade_date >= start_date,
                IndexDailyBarModel.trade_date <= end_date,
            )
            .all()
        }
        factor_dates = {
            r[0]
            for r in self._db.query(IndexFactorValueModel.trade_date)
            .filter(
                IndexFactorValueModel.factor_id == factor_id,
                IndexFactorValueModel.index_code == index_code,
                IndexFactorValueModel.trade_date >= start_date,
                IndexFactorValueModel.trade_date <= end_date,
                IndexFactorValueModel.strategy_id.is_(None),
            )
            .all()
        }
        return sorted(bar_dates - factor_dates)

    def find_all_bar_dates(
        self,
        index_code: str,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        """查询指定指数在 [start, end] 范围内所有有行情数据的日期。

        用于强制重新计算时获取需要计算的日期列表。

        Args:
            index_code: 指数代码。
            start_date: 开始日期（含）。
            end_date: 截止日期（含）。

        Returns:
            有行情数据的交易日列表，按日期升序排列。
        """
        return sorted(
            r[0]
            for r in self._db.query(IndexDailyBarModel.trade_date)
            .filter(
                IndexDailyBarModel.index_code == index_code,
                IndexDailyBarModel.trade_date >= start_date,
                IndexDailyBarModel.trade_date <= end_date,
            )
            .all()
        )

    def find_missing_dates_for_all_indexes(
        self,
        factor_id: str,
        trade_date: date,
    ) -> list[str]:
        """查询指定交易日有行情数据但缺失指定因子值的指数代码列表。

        Args:
            factor_id: 因子标识。
            trade_date: 交易日。

        Returns:
            缺失因子值的指数代码列表。
        """
        bar_codes = {
            r[0]
            for r in self._db.query(IndexDailyBarModel.index_code)
            .filter(IndexDailyBarModel.trade_date == trade_date)
            .all()
        }
        factor_codes = {
            r[0]
            for r in self._db.query(IndexFactorValueModel.index_code)
            .filter(
                IndexFactorValueModel.factor_id == factor_id,
                IndexFactorValueModel.trade_date == trade_date,
                IndexFactorValueModel.strategy_id.is_(None),
            )
            .all()
        }
        return sorted(bar_codes - factor_codes)
