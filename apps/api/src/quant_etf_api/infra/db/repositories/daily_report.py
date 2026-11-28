"""每日量化研究简报的数据库读取与版本写入。"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    IndexDailyBarModel,
    IndexMemberEventModel,
    MacroIndicatorModel,
    MarketIndicatorModel,
    TradingCalendarModel,
)
from quant_etf_api.infra.db.models.daily_report import MarketDailyReportModel
from quant_etf_api.infra.db.models.industry import (
    IndustryDailyBarModel,
    IndustryMembershipEventModel,
    IndustryUniverseModel,
    StockDailyCloseModel,
)
from quant_etf_api.infra.db.models.stock import (
    StockDailyBasicModel,
    StockMoneyflowModel,
    StockUniverseModel,
)


class DailyReportRepository:
    """封装日报事实查询与已发布版本持久化。"""

    def __init__(self, db: Session) -> None:
        """初始化仓库。

        Args:
            db: SQLAlchemy 会话。
        """
        self._db = db

    def latest_market_date(self, upper_bound: date) -> date | None:
        """读取不晚于指定日期的最近个股行情日期。"""
        return (
            self._db.query(func.max(StockDailyCloseModel.trade_date))
            .filter(StockDailyCloseModel.trade_date <= upper_bound)
            .scalar()
        )

    def trading_dates(self, end_date: date, limit: int) -> list[date]:
        """读取不晚于指定日期的最近交易日列表（升序）。"""
        rows = (
            self._db.query(StockDailyCloseModel.trade_date)
            .filter(StockDailyCloseModel.trade_date <= end_date)
            .group_by(StockDailyCloseModel.trade_date)
            .order_by(StockDailyCloseModel.trade_date.desc())
            .limit(limit)
            .all()
        )
        return sorted(row[0] for row in rows)

    def next_trading_date(self, target: date) -> date | None:
        """读取目标日期之后最近的交易日。"""
        return (
            self._db.query(func.min(TradingCalendarModel.trade_date))
            .filter(
                TradingCalendarModel.trade_date > target,
                TradingCalendarModel.is_trading_day.is_(True),
            )
            .scalar()
        )

    def is_trading_date(self, target: date) -> bool:
        """判断目标日期是否在本地交易日历中。"""
        return (
            self._db.query(TradingCalendarModel.trade_date)
            .filter(
                TradingCalendarModel.trade_date == target,
                TradingCalendarModel.is_trading_day.is_(True),
            )
            .first()
            is not None
        )

    def stock_market_daily(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """按交易日聚合个股广度、中位涨跌幅和成交额。"""
        rows = (
            self._db.query(
                StockDailyCloseModel.trade_date.label("trade_date"),
                func.count().label("total"),
                func.count(case((StockDailyCloseModel.pct_chg > 0, 1))).label("advances"),
                func.count(case((StockDailyCloseModel.pct_chg == 0, 1))).label("unchanged"),
                func.count(case((StockDailyCloseModel.pct_chg < 0, 1))).label("declines"),
                func.percentile_cont(0.5)
                .within_group(StockDailyCloseModel.pct_chg)
                .label("median_change_pct"),
                (func.sum(StockDailyCloseModel.amount) / 100000.0).label("turnover_billion"),
            )
            .filter(
                StockDailyCloseModel.trade_date >= start_date,
                StockDailyCloseModel.trade_date <= end_date,
                StockDailyCloseModel.pct_chg.is_not(None),
            )
            .group_by(StockDailyCloseModel.trade_date)
            .order_by(StockDailyCloseModel.trade_date)
            .all()
        )
        return [dict(row._mapping) for row in rows]

    def limit_daily(self, start_date: date, end_date: date) -> dict[date, tuple[int, int]]:
        """按交易日读取涨停和跌停家数。"""
        rows = (
            self._db.query(
                StockDailyBasicModel.trade_date,
                func.count(case((StockDailyBasicModel.limit_status.in_((2, 3)), 1))).label(
                    "limit_up"
                ),
                func.count(case((StockDailyBasicModel.limit_status.in_((5, 6)), 1))).label(
                    "limit_down"
                ),
            )
            .filter(
                StockDailyBasicModel.trade_date >= start_date,
                StockDailyBasicModel.trade_date <= end_date,
            )
            .group_by(StockDailyBasicModel.trade_date)
            .all()
        )
        return {row[0]: (int(row[1] or 0), int(row[2] or 0)) for row in rows}

    def index_daily(
        self, index_codes: list[str], start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        """读取指定指数区间内的日线行情。"""
        rows = (
            self._db.query(IndexDailyBarModel)
            .filter(
                IndexDailyBarModel.index_code.in_(index_codes),
                IndexDailyBarModel.trade_date >= start_date,
                IndexDailyBarModel.trade_date <= end_date,
            )
            .order_by(IndexDailyBarModel.index_code, IndexDailyBarModel.trade_date)
            .all()
        )
        return [dict(row.__dict__) for row in rows]

    def index_names(self, index_codes: list[str]) -> dict[str, str]:
        """读取指数中文名。"""
        rows = (
            self._db.query(BenchmarkIndexModel.index_code, BenchmarkIndexModel.name_cn)
            .filter(BenchmarkIndexModel.index_code.in_(index_codes))
            .all()
        )
        return {row[0]: row[1] for row in rows}

    def pcr_daily(self, start_date: date, end_date: date) -> dict[date, float]:
        """读取 300 期权成交量 PCR。"""
        rows = (
            self._db.query(MarketIndicatorModel.trade_date, MarketIndicatorModel.value)
            .filter(
                MarketIndicatorModel.indicator_code == "opt_io_pcr_vol",
                MarketIndicatorModel.trade_date >= start_date,
                MarketIndicatorModel.trade_date <= end_date,
            )
            .all()
        )
        return {row[0]: float(row[1]) for row in rows}

    def market_indicators_on(self, target: date) -> dict[str, dict[str, Any]]:
        """读取目标日期可用的市场指标。"""
        rows = (
            self._db.query(MarketIndicatorModel)
            .filter(MarketIndicatorModel.trade_date == target)
            .all()
        )
        return {row.indicator_code: dict(row.__dict__) for row in rows}

    def moneyflow_daily(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """按交易日汇总主力净流入与全单净流入。"""
        main_net = (
            func.sum(StockMoneyflowModel.buy_lg_amount)
            + func.sum(StockMoneyflowModel.buy_elg_amount)
            - func.sum(StockMoneyflowModel.sell_lg_amount)
            - func.sum(StockMoneyflowModel.sell_elg_amount)
        )
        rows = (
            self._db.query(
                StockMoneyflowModel.trade_date.label("trade_date"),
                (main_net / 10000.0).label("main_net_billion"),
                (func.sum(StockMoneyflowModel.net_mf_amount) / 10000.0).label(
                    "all_net_billion"
                ),
            )
            .filter(
                StockMoneyflowModel.trade_date >= start_date,
                StockMoneyflowModel.trade_date <= end_date,
            )
            .group_by(StockMoneyflowModel.trade_date)
            .order_by(StockMoneyflowModel.trade_date)
            .all()
        )
        return [dict(row._mapping) for row in rows]

    def industry_daily(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """读取申万一级行业区间行情。"""
        rows = (
            self._db.query(IndustryDailyBarModel, IndustryUniverseModel.name_cn)
            .join(
                IndustryUniverseModel,
                IndustryUniverseModel.industry_code == IndustryDailyBarModel.industry_code,
            )
            .filter(
                IndustryDailyBarModel.trade_date >= start_date,
                IndustryDailyBarModel.trade_date <= end_date,
            )
            .order_by(IndustryDailyBarModel.trade_date, IndustryDailyBarModel.industry_code)
            .all()
        )
        return [
            {
                "trade_date": bar.trade_date,
                "industry_code": bar.industry_code,
                "name": name,
                "change_pct": bar.change_pct,
                "close_price": bar.close_price,
            }
            for bar, name in rows
        ]

    def macro_rows(self) -> list[dict[str, Any]]:
        """读取宏观指标历史，供服务层取最近两期。"""
        rows = self._db.query(MacroIndicatorModel).all()
        return [dict(row.__dict__) for row in rows]

    def stock_rows(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """读取区间个股行情。"""
        rows = (
            self._db.query(StockDailyCloseModel)
            .filter(
                StockDailyCloseModel.trade_date >= start_date,
                StockDailyCloseModel.trade_date <= end_date,
            )
            .all()
        )
        return [dict(row.__dict__) for row in rows]

    def stock_basic_on(self, target: date) -> dict[str, dict[str, Any]]:
        """读取目标交易日个股每日指标。"""
        rows = (
            self._db.query(StockDailyBasicModel)
            .filter(StockDailyBasicModel.trade_date == target)
            .all()
        )
        return {row.stock_code: dict(row.__dict__) for row in rows}

    def stock_names(self) -> dict[str, str]:
        """读取股票中文名。"""
        rows = self._db.query(StockUniverseModel.stock_code, StockUniverseModel.name_cn).all()
        return {row[0]: row[1] for row in rows}

    def latest_industry_by_stock(self, target: date) -> dict[str, tuple[str, str]]:
        """读取目标日期前每个股票的最新申万行业归属。"""
        latest_start = (
            self._db.query(
                IndustryMembershipEventModel.stock_code.label("stock_code"),
                func.max(IndustryMembershipEventModel.start_date).label("start_date"),
            )
            .filter(IndustryMembershipEventModel.start_date <= target)
            .group_by(IndustryMembershipEventModel.stock_code)
            .subquery()
        )
        rows = (
            self._db.query(
                IndustryMembershipEventModel.stock_code,
                IndustryMembershipEventModel.industry_code,
                IndustryUniverseModel.name_cn,
            )
            .join(
                latest_start,
                (IndustryMembershipEventModel.stock_code == latest_start.c.stock_code)
                & (IndustryMembershipEventModel.start_date == latest_start.c.start_date),
            )
            .join(
                IndustryUniverseModel,
                IndustryUniverseModel.industry_code
                == IndustryMembershipEventModel.industry_code,
            )
            .all()
        )
        return {row[0]: (row[1], row[2]) for row in rows}

    def latest_index_memberships(self, target: date) -> list[dict[str, Any]]:
        """读取每个指数在目标日期前的最新成分快照。"""
        latest_start = (
            self._db.query(
                IndexMemberEventModel.index_code.label("index_code"),
                func.max(IndexMemberEventModel.start_date).label("start_date"),
            )
            .filter(IndexMemberEventModel.start_date <= target)
            .group_by(IndexMemberEventModel.index_code)
            .subquery()
        )
        rows = (
            self._db.query(
                IndexMemberEventModel.index_code,
                IndexMemberEventModel.stock_code,
                BenchmarkIndexModel.name_cn,
            )
            .join(
                latest_start,
                (IndexMemberEventModel.index_code == latest_start.c.index_code)
                & (IndexMemberEventModel.start_date == latest_start.c.start_date),
            )
            .join(
                BenchmarkIndexModel,
                BenchmarkIndexModel.index_code == IndexMemberEventModel.index_code,
            )
            .all()
        )
        return [
            {"index_code": row[0], "stock_code": row[1], "name": row[2]} for row in rows
        ]

    def latest_published(
        self, report_date: date, revision_no: int | None = None
    ) -> MarketDailyReportModel | None:
        """读取指定日期已发布版本或指定历史修订。"""
        query = self._db.query(MarketDailyReportModel).filter(
            MarketDailyReportModel.report_date == report_date
        )
        if revision_no is not None:
            query = query.filter(MarketDailyReportModel.revision_no == revision_no)
        else:
            query = query.filter(MarketDailyReportModel.status == "published")
        return query.one_or_none()

    def list_reports(self, limit: int) -> list[MarketDailyReportModel]:
        """按日期倒序读取已发布日报摘要行。"""
        return (
            self._db.query(MarketDailyReportModel)
            .filter(MarketDailyReportModel.status == "published")
            .order_by(
                MarketDailyReportModel.report_date.desc(),
                MarketDailyReportModel.revision_no.desc(),
            )
            .limit(limit)
            .all()
        )

    def next_revision(self, report_date: date) -> int:
        """返回同日下一个修订号。"""
        current = (
            self._db.query(func.max(MarketDailyReportModel.revision_no))
            .filter(MarketDailyReportModel.report_date == report_date)
            .scalar()
        )
        return int(current or 0) + 1

    def supersede_published(self, report_date: date) -> None:
        """将同日现有发布版本标记为历史版本。"""
        self._db.query(MarketDailyReportModel).filter(
            MarketDailyReportModel.report_date == report_date,
            MarketDailyReportModel.status == "published",
        ).update({MarketDailyReportModel.status: "superseded"}, synchronize_session=False)

    def add(self, row: MarketDailyReportModel) -> None:
        """把新修订加入当前事务。"""
        self._db.add(row)

    def flush(self) -> None:
        """刷新当前事务，触发数据库约束。"""
        self._db.flush()

    def rollback(self) -> None:
        """回滚当前事务。"""
        self._db.rollback()
