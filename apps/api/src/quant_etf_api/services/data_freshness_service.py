"""数据新鲜度查询服务。

负责汇总指数、个股和行业行情的数据覆盖状态，不负责数据摄取或写入。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import IndexDailyBarModel
from quant_etf_api.infra.db.models.industry import (
    IndustryDailyBarModel,
    IndustryUniverseModel,
    StockDailyCloseModel,
)
from quant_etf_api.infra.db.models.stock import StockUniverseModel
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.data_health import DataHealthSnapshotRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.time import today_cn
from quant_etf_api.infra.trading_calendar import TradingCalendar


class DataFreshnessService:
    """汇总各类行情数据的新鲜度和覆盖率。"""

    def __init__(self, db: Session) -> None:
        """初始化数据新鲜度服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._health_repo = DataHealthSnapshotRepository(db)
        self._index_bar_repo = IndexDailyBarRepository(db)
        self._valuation_repo = IndexValuationRepository(db)
        self._index_repo = BenchmarkIndexRepository(db)

    def check_data_freshness(self) -> dict[str, Any]:
        """检查各数据表的新鲜度和覆盖率。

        针对每个基准指数，检查对应数据表中是否有记录、
        最新数据日期距今是否超过 3 个自然日（节假日容忍），返回汇总结果。
        """
        today = today_cn()
        cal = TradingCalendar()
        # 使用最近交易日作为新鲜度基准，容忍 1 个交易日间隔
        latest_td = cal.latest_trading_day(today)
        stale_threshold = latest_td - timedelta(days=1)
        result: dict[str, Any] = {}

        # --- 指数日线 ---
        indexes = self._index_repo.find_all()
        idx_bar_stale = []
        idx_bar_missing = []
        idx_bar_latest: date | None = None
        for idx in indexes:
            max_d = self._index_bar_repo.get_latest_date(idx.index_code)
            if max_d is None:
                idx_bar_missing.append(
                    {
                        "code": idx.index_code,
                        "name": idx.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
            else:
                if idx_bar_latest is None or max_d > idx_bar_latest:
                    idx_bar_latest = max_d
                if max_d < stale_threshold:
                    idx_bar_stale.append(
                        {
                            "code": idx.index_code,
                            "name": idx.name_cn,
                            "latest_date": str(max_d),
                            "is_stale": True,
                        }
                    )

        result["index_bars"] = {
            "total": len(indexes),
            "up_to_date": len(indexes) - len(idx_bar_stale) - len(idx_bar_missing),
            "stale": idx_bar_stale,
            "missing": idx_bar_missing,
            "latest_date": str(idx_bar_latest) if idx_bar_latest else None,
        }

        # --- 指数估值 ---
        idx_val_stale = []
        idx_val_missing = []
        idx_val_latest: date | None = None
        for idx in indexes:
            max_d = self._valuation_repo.get_latest_date(idx.index_code)
            if max_d is None:
                idx_val_missing.append(
                    {
                        "code": idx.index_code,
                        "name": idx.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
            else:
                if idx_val_latest is None or max_d > idx_val_latest:
                    idx_val_latest = max_d
                if max_d < stale_threshold:
                    idx_val_stale.append(
                        {
                            "code": idx.index_code,
                            "name": idx.name_cn,
                            "latest_date": str(max_d),
                            "is_stale": True,
                        }
                    )

        result["index_valuation"] = {
            "total": len(indexes),
            "up_to_date": len(indexes) - len(idx_val_stale) - len(idx_val_missing),
            "stale": idx_val_stale,
            "missing": idx_val_missing,
            "latest_date": str(idx_val_latest) if idx_val_latest else None,
        }

        # --- 个股日线（基于统一数据健康快照） ---
        stock_rows = self._db.query(StockUniverseModel).all()
        stock_health = self._health_repo.find_by_dataset_and_partitions(
            "stock_daily_close", [stock.stock_code for stock in stock_rows]
        )
        stock_stale: list[dict[str, Any]] = []
        stock_missing: list[dict[str, Any]] = []
        for stock in stock_rows:
            health = stock_health.get(stock.stock_code)
            if (
                health is None
                or health.last_checked_at is None
                or health.record_count == 0
                or health.latest_date is None
            ):
                stock_missing.append(
                    {
                        "code": stock.stock_code,
                        "name": stock.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
                continue
            if health.latest_date < stale_threshold:
                stock_stale.append(
                    {
                        "code": stock.stock_code,
                        "name": stock.name_cn,
                        "latest_date": str(health.latest_date),
                        "is_stale": True,
                    }
                )
        stock_latest_db = self._db.query(func.max(StockDailyCloseModel.trade_date)).scalar()
        result["stock_bars"] = {
            "total": len(stock_rows),
            "up_to_date": len(stock_rows) - len(stock_stale) - len(stock_missing),
            "stale": stock_stale[:3],
            "missing": stock_missing[:3],
            "stale_total": len(stock_stale),
            "missing_total": len(stock_missing),
            "latest_date": str(stock_latest_db) if stock_latest_db else None,
        }

        # --- 行业日线（基于 industry_universe 质量快照聚合） ---
        industry_rows = self._db.query(IndustryUniverseModel).all()
        industry_stale: list[dict[str, Any]] = []
        industry_missing: list[dict[str, Any]] = []
        for industry in industry_rows:
            if (
                industry.quality_checked_at is None
                or industry.bar_count is None
                or industry.bar_count == 0
                or industry.data_end_date is None
            ):
                industry_missing.append(
                    {
                        "code": industry.industry_code,
                        "name": industry.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
                continue
            if industry.data_end_date < stale_threshold:
                industry_stale.append(
                    {
                        "code": industry.industry_code,
                        "name": industry.name_cn,
                        "latest_date": str(industry.data_end_date),
                        "is_stale": True,
                    }
                )
        industry_latest_db = self._db.query(
            func.max(IndustryDailyBarModel.trade_date)
        ).scalar()
        result["industry_bars"] = {
            "total": len(industry_rows),
            "up_to_date": len(industry_rows) - len(industry_stale) - len(industry_missing),
            "stale": industry_stale[:3],
            "missing": industry_missing[:3],
            "stale_total": len(industry_stale),
            "missing_total": len(industry_missing),
            "latest_date": str(industry_latest_db) if industry_latest_db else None,
        }

        # --- 字段级质量 ---
        total_index_bars = self._db.query(func.count(IndexDailyBarModel.id)).scalar() or 0
        null_index_change_pct = (
            self._db.query(func.count(IndexDailyBarModel.id))
            .filter(IndexDailyBarModel.change_pct.is_(None))
            .scalar()
            or 0
        )
        result["field_quality"] = {
            "index_bars": {
                "total_records": total_index_bars,
                "change_pct_null": null_index_change_pct,
                "change_pct_null_rate": round(null_index_change_pct / total_index_bars, 4)
                if total_index_bars
                else 0,
            },
        }

        result["checked_at"] = utcnow().isoformat()
        return result

