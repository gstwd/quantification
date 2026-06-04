from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    EtfDailyBarModel,
    EtfDailyShareModel,
    IndexDailyBarModel,
    IndexValuationModel,
    MacroIndicatorModel,
)
from quant_etf_api.infra.db.repositories.etf_universe import EtfUniverseRepository
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.schemas.run import ResearchRunSummary
from quant_etf_api.schemas.system import DataSourceSnapshot, SystemStatusResponse

logger = logging.getLogger(__name__)


class SystemService:
    """系统状态查询服务。

    从数据库各表中聚合数据概览、数据源新鲜度、最近运行记录和连接状态，
    供前端"数据状态"页面展示。
    """

    def __init__(
        self,
        db: Session,
        universe_repo: EtfUniverseRepository | None = None,
        run_repo: ResearchRunRepository | None = None,
    ) -> None:
        self._db = db
        self._universe_repo = universe_repo or EtfUniverseRepository(db)
        self._run_repo = run_repo or ResearchRunRepository(db)

    def _check_db_connection(self) -> bool:
        """通过执行轻量查询检测数据库是否可达。"""
        try:
            self._db.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.warning("数据库连接检测失败", exc_info=True)
            return False

    def _get_active_etf_count(self) -> int:
        """查询当前活跃 ETF 数量。"""
        try:
            return self._universe_repo.count_active()
        except Exception:
            logger.warning("活跃ETF数量查询失败", exc_info=True)
            return 0

    def _get_table_snapshot(
        self,
        model: type,
        source_name: str,
        table_name: str,
        date_column: str = "trade_date",
    ) -> DataSourceSnapshot:
        """查询单张数据表的统计快照。

        Args:
            model: SQLAlchemy 模型类（如 EtfDailyBarModel）。
            source_name: 数据源展示名称（如 "新浪日线行情"）。
            table_name: 数据库表名（如 "etf_daily_bar"）。
            date_column: 用于获取最新日期的列名，默认 "trade_date"。

        Returns:
            DataSourceSnapshot，查询失败时返回全零值快照。
        """
        try:
            result = self._db.query(
                func.count().label("cnt"),
                func.max(getattr(model, date_column)).label("max_date"),
                func.max(model.ingested_at).label("max_ingested"),
            ).one()
            return DataSourceSnapshot(
                source_name=source_name,
                table_name=table_name,
                record_count=result.cnt or 0,
                latest_trade_date=result.max_date,
                latest_ingested_at=result.max_ingested,
            )
        except Exception:
            logger.warning("表 %s 快照查询失败", table_name, exc_info=True)
            return DataSourceSnapshot(
                source_name=source_name,
                table_name=table_name,
                record_count=0,
                latest_trade_date=None,
                latest_ingested_at=None,
            )

    def _get_recent_runs(self, limit: int = 5) -> list[ResearchRunSummary]:
        """获取最近 N 条研究运行记录。"""
        try:
            rows = self._run_repo.find_recent(limit=limit)
            return [
                ResearchRunSummary(
                    run_id=r.run_id,
                    run_type=r.run_type,
                    strategy_id=r.strategy_id,
                    trade_date=r.trade_date,
                    status=r.status,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                    error_message=r.error_message,
                )
                for r in rows
            ]
        except Exception:
            logger.warning("最近运行记录查询失败", exc_info=True)
            return []

    def status(self) -> SystemStatusResponse:
        """聚合系统运行状态快照。

        并行收集各维度数据：数据库连接、ETF数量、各表快照、
        最近运行记录。任一查询失败不影响其他查询结果，
        对应字段返回零值或空列表。

        Returns:
            包含完整系统状态的响应对象。
        """
        db_connected = self._check_db_connection()

        if not db_connected:
            # 数据库不可达时直接返回降级状态，不再尝试后续查询
            return SystemStatusResponse(
                active_etf_count=0,
                latest_trade_date=None,
                data_sources=[],
                recent_runs=[],
                db_connected=False,
            )

        active_etf_count = self._get_active_etf_count()

        data_sources = [
            self._get_table_snapshot(
                EtfDailyBarModel,
                source_name="新浪日线行情",
                table_name="etf_daily_bar",
            ),
            self._get_table_snapshot(
                EtfDailyShareModel,
                source_name="东方财富份额",
                table_name="etf_daily_share",
            ),
            self._get_table_snapshot(
                IndexDailyBarModel,
                source_name="指数日线行情",
                table_name="index_daily_bar",
            ),
            self._get_table_snapshot(
                IndexValuationModel,
                source_name="指数估值PE/PB",
                table_name="index_valuation",
            ),
            self._get_table_snapshot(
                MacroIndicatorModel,
                source_name="宏观经济指标",
                table_name="macro_indicator",
                date_column="period",
            ),
        ]

        # 全局最新交易日：取各表中非 None 的最大值
        latest_trade_date: date | None = None
        for s in data_sources:
            if s.latest_trade_date is not None:
                if latest_trade_date is None or s.latest_trade_date > latest_trade_date:
                    latest_trade_date = s.latest_trade_date

        recent_runs = self._get_recent_runs(limit=5)

        return SystemStatusResponse(
            active_etf_count=active_etf_count,
            latest_trade_date=latest_trade_date,
            data_sources=data_sources,
            recent_runs=recent_runs,
            db_connected=True,
        )
