"""因子定义仓库，提供因子元数据和因子值的查询方法。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    EtfDailyBarModel,
    EtfFactorValueModel,
    EtfUniverseModel,
    FactorDefinitionModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository


class FactorDefinitionRepository(BaseRepository):
    """FactorDefinitionModel 的查询仓库。"""

    def __init__(self, db: Session) -> None:
        """初始化因子定义仓库。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        super().__init__(db)

    def find_all(self) -> list[FactorDefinitionModel]:
        """返回所有因子定义（含已禁用），按 factor_id 排序。"""
        return (
            self._db.query(FactorDefinitionModel)
            .order_by(FactorDefinitionModel.factor_id)
            .all()
        )

    def find_active(self) -> list[FactorDefinitionModel]:
        """返回所有启用的因子定义，供计算使用。"""
        return (
            self._db.query(FactorDefinitionModel)
            .filter(FactorDefinitionModel.is_active.is_(True))
            .order_by(FactorDefinitionModel.factor_id)
            .all()
        )

    def find_by_id(self, factor_id: str) -> FactorDefinitionModel | None:
        """按主键查询单条因子定义。"""
        return self._db.get(FactorDefinitionModel, factor_id)

    def find_latest_date(self, factor_id: str) -> date | None:
        """查询指定因子在 etf_factor_value 中的最大 trade_date。

        Args:
            factor_id: 因子标识。

        Returns:
            最新交易日，无数据时返回 None。
        """
        result = (
            self._db.query(func.max(EtfFactorValueModel.trade_date))
            .filter(EtfFactorValueModel.factor_id == factor_id)
            .scalar()
        )
        return result

    def find_latest_any_factor(self) -> date | None:
        """查询 etf_factor_value 中任意因子的最大 trade_date。

        Returns:
            最新交易日，无数据时返回 None。
        """
        result = self._db.query(func.max(EtfFactorValueModel.trade_date)).scalar()
        return result

    def find_latest_bar_date(self) -> date | None:
        """查询 etf_daily_bar 中的最大 trade_date。

        Returns:
            最新行情日期，无数据时返回 None。
        """
        result = self._db.query(func.max(EtfDailyBarModel.trade_date)).scalar()
        return result

    def find_cross_section(
        self, factor_id: str, trade_date: date
    ) -> list[tuple[str, str, float | None, str | None]]:
        """查询指定因子在指定交易日的横截面数据，JOIN ETF 中文名。

        Args:
            factor_id: 因子标识。
            trade_date: 交易日。

        Returns:
            (etf_code, name_cn, factor_value_numeric, factor_value_text) 元组列表。
        """
        rows = (
            self._db.query(
                EtfFactorValueModel.etf_code,
                EtfUniverseModel.name_cn,
                EtfFactorValueModel.factor_value_numeric,
                EtfFactorValueModel.factor_value_text,
            )
            .join(EtfUniverseModel, EtfFactorValueModel.etf_code == EtfUniverseModel.etf_code)
            .filter(
                EtfFactorValueModel.factor_id == factor_id,
                EtfFactorValueModel.trade_date == trade_date,
                EtfFactorValueModel.strategy_id.is_(None),
            )
            .order_by(EtfFactorValueModel.etf_code)
            .all()
        )
        return rows

    def find_factor_values(
        self,
        factor_id: str,
        etf_code: str,
        start_date: date,
        end_date: date,
    ) -> list[EtfFactorValueModel]:
        """查询单因子在单 ETF 上的时间序列。

        Args:
            factor_id: 因子标识。
            etf_code: ETF 代码。
            start_date: 开始日期（含）。
            end_date: 结束日期（含）。

        Returns:
            按 trade_date 升序排列的 EtfFactorValueModel 列表。
        """
        return (
            self._db.query(EtfFactorValueModel)
            .filter(
                EtfFactorValueModel.factor_id == factor_id,
                EtfFactorValueModel.etf_code == etf_code,
                EtfFactorValueModel.trade_date >= start_date,
                EtfFactorValueModel.trade_date <= end_date,
                EtfFactorValueModel.strategy_id.is_(None),
            )
            .order_by(EtfFactorValueModel.trade_date.asc())
            .all()
        )

    def find_missing_dates(
        self,
        factor_id: str,
        etf_code: str,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        """查询指定 ETF 在 [start, end] 范围内有行情数据但缺失因子值的日期。

        Args:
            factor_id: 因子标识。
            etf_code: ETF 代码。
            start_date: 开始日期（含）。
            end_date: 结束日期（含）。

        Returns:
            缺失因子值的交易日列表，按日期升序排列。
        """
        # 有行情数据的日期
        bar_dates = {
            r[0]
            for r in self._db.query(EtfDailyBarModel.trade_date)
            .filter(
                EtfDailyBarModel.etf_code == etf_code,
                EtfDailyBarModel.trade_date >= start_date,
                EtfDailyBarModel.trade_date <= end_date,
            )
            .all()
        }
        # 已有因子值的日期
        factor_dates = {
            r[0]
            for r in self._db.query(EtfFactorValueModel.trade_date)
            .filter(
                EtfFactorValueModel.factor_id == factor_id,
                EtfFactorValueModel.etf_code == etf_code,
                EtfFactorValueModel.trade_date >= start_date,
                EtfFactorValueModel.trade_date <= end_date,
                EtfFactorValueModel.strategy_id.is_(None),
            )
            .all()
        }
        return sorted(bar_dates - factor_dates)

    def find_missing_dates_for_all_etfs(
        self,
        factor_id: str,
        trade_date: date,
    ) -> list[str]:
        """查询指定交易日有行情数据但缺失指定因子值的 ETF 代码列表。

        Args:
            factor_id: 因子标识。
            trade_date: 交易日。

        Returns:
            缺失因子值的 ETF 代码列表。
        """
        bar_codes = {
            r[0]
            for r in self._db.query(EtfDailyBarModel.etf_code)
            .filter(EtfDailyBarModel.trade_date == trade_date)
            .all()
        }
        factor_codes = {
            r[0]
            for r in self._db.query(EtfFactorValueModel.etf_code)
            .filter(
                EtfFactorValueModel.factor_id == factor_id,
                EtfFactorValueModel.trade_date == trade_date,
                EtfFactorValueModel.strategy_id.is_(None),
            )
            .all()
        }
        return sorted(bar_codes - factor_codes)
