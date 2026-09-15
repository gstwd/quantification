from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, literal, null, text
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    IndexDailyBarModel,
    IndexValuationModel,
    MacroIndicatorModel,
)
from quant_etf_api.infra.db.models.industry import (
    IndustryDailyBarModel,
    IndustryMembershipEventModel,
    StockDailyCloseModel,
)
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.schemas.run import ResearchRunSummary
from quant_etf_api.schemas.system import DataSourceSnapshot, SystemStatusResponse

logger = logging.getLogger(__name__)


class SystemService:
    """系统状态查询服务。

    从数据库各表中聚合数据概览、数据源新鲜度、最近运行记录和连接状态，
    供前端"数据状态"页面展示。

    性能约定：状态接口是全页首屏的必调接口，因此不做任何全表扫描。
    超大表的记录数改用 PostgreSQL 统计信息中的行数估算值（见
    ``_ESTIMATED_COUNT_THRESHOLD``），最新业务日期与最近入库时间依赖
    btree 索引上的 ``max()`` 反向扫描（见迁移 0050）。
    """

    # 精确 count(*) 在 PostgreSQL 中必须扫描全表：stock_daily_close（约 1250 万行）
    # 单次约 3.5 秒，且随数据增长线性变慢。估算行数达到该阈值的表改用
    # pg_class.reltuples 统计值（由 ANALYZE/autovacuum 维护，实测误差 <1%），
    # 小表仍返回精确计数，兼顾准确性与首屏响应速度。
    _ESTIMATED_COUNT_THRESHOLD = 500_000

    def __init__(
        self,
        db: Session,
        index_repo: BenchmarkIndexRepository | None = None,
        run_repo: ResearchRunRepository | None = None,
    ) -> None:
        self._db = db
        self._index_repo = index_repo or BenchmarkIndexRepository(db)
        self._run_repo = run_repo or ResearchRunRepository(db)

    def _check_db_connection(self) -> bool:
        """通过执行轻量查询检测数据库是否可达。"""
        try:
            self._db.execute(text("SELECT 1"))
            return True
        except Exception:
            self._db.rollback()
            logger.warning("数据库连接检测失败", exc_info=True)
            return False

    def _get_active_index_count(self) -> int:
        """查询当前活跃指数数量。"""
        try:
            return self._index_repo.count_active()
        except Exception:
            self._db.rollback()
            logger.warning("活跃指数数量查询失败", exc_info=True)
            return 0

    def _load_row_estimates(self, table_names: list[str]) -> dict[str, int]:
        """批量读取各表的行数统计估算值。

        一次查询取回所有目标表的 ``pg_class.reltuples``，避免为每张表单独
        发起一次往返。统计信息缺失（reltuples <= 0，表尚未被 ANALYZE 过）
        的表不会出现在返回值中，调用方据此回退到精确计数。

        Args:
            table_names: 需要读取估算行数的表名列表。

        Returns:
            表名 → 估算行数的映射，读取失败时返回空字典。
        """
        if not table_names:
            return {}
        try:
            rows = self._db.execute(
                text(
                    "SELECT c.relname AS relname, c.reltuples AS reltuples "
                    "FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relkind = 'r' AND n.nspname = current_schema() "
                    "AND c.relname = ANY(:names)"
                ),
                {"names": table_names},
            ).all()
            return {
                row.relname: int(row.reltuples)
                for row in rows
                if row.reltuples is not None and row.reltuples > 0
            }
        except Exception:
            self._db.rollback()
            logger.warning("表行数统计读取失败", exc_info=True)
            return {}

    def _get_table_snapshot(
        self,
        model: type,
        source_name: str,
        table_name: str,
        date_column: str = "trade_date",
        ingested_column: str = "ingested_at",
        estimated_rows: int | None = None,
    ) -> DataSourceSnapshot:
        """查询单张数据表的统计快照。

        Args:
            model: SQLAlchemy 模型类（如 IndexDailyBarModel）。
            source_name: 数据源展示名称（如 "新浪日线行情"）。
            table_name: 数据库表名（如 "index_daily_bar"）。
            date_column: 用于获取最新日期的列名，默认 "trade_date"。
                None 表示该表无业务日期维度（如成分事件，只展示入库时间）。
            ingested_column: 用于获取最近入库时间的列名，默认 "ingested_at"。
            estimated_rows: 该表的统计估算行数。达到 ``_ESTIMATED_COUNT_THRESHOLD``
                时直接返回估算值而不执行 ``count(*)``，避免全表扫描。

        Returns:
            DataSourceSnapshot，查询失败时返回全零值快照。
        """
        try:
            # 估算值足够大时跳过精确计数：count(*) 无索引可用，只能全表扫描
            count_label = (
                literal(estimated_rows).label("cnt")
                if estimated_rows is not None and estimated_rows >= self._ESTIMATED_COUNT_THRESHOLD
                else func.count().label("cnt")
            )
            query = self._db.query(count_label)
            if date_column is not None:
                query = query.add_columns(
                    func.max(getattr(model, date_column)).label("max_date")
                )
            else:
                query = query.add_columns(null().label("max_date"))
            query = query.add_columns(
                func.max(getattr(model, ingested_column)).label("max_ingested")
            )
            result = query.one()
            return DataSourceSnapshot(
                source_name=source_name,
                table_name=table_name,
                record_count=result.cnt or 0,
                latest_trade_date=result.max_date if date_column is not None else None,
                latest_ingested_at=result.max_ingested,
            )
        except Exception:
            self._db.rollback()
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
            self._db.rollback()
            logger.warning("最近运行记录查询失败", exc_info=True)
            return []

    def status(self) -> SystemStatusResponse:
        """聚合系统运行状态快照。

        并行收集各维度数据：数据库连接、指数数量、各表快照、
        最近运行记录。任一查询失败不影响其他查询结果，
        对应字段返回零值或空列表。

        各表快照的记录数不做全表扫描：先一次性读取行数统计估算值，
        估算值超过阈值的表直接采用估算结果。

        Returns:
            包含完整系统状态的响应对象。
        """
        db_connected = self._check_db_connection()

        if not db_connected:
            # 数据库不可达时直接返回降级状态，不再尝试后续查询
            return SystemStatusResponse(
                active_index_count=0,
                latest_trade_date=None,
                data_sources=[],
                recent_runs=[],
                db_connected=False,
            )

        active_index_count = self._get_active_index_count()

        # 表名清单同时用于行数统计批量查询与后续快照查询
        snapshot_table_names = [
            "index_daily_bar",
            "index_valuation",
            "macro_indicator",
            "stock_daily_close",
            "industry_daily_bar",
            "industry_membership_event",
        ]
        row_estimates = self._load_row_estimates(snapshot_table_names)

        data_sources = [
            self._get_table_snapshot(
                IndexDailyBarModel,
                source_name="指数日线行情",
                table_name="index_daily_bar",
                estimated_rows=row_estimates.get("index_daily_bar"),
            ),
            self._get_table_snapshot(
                IndexValuationModel,
                source_name="指数估值PE/PB",
                table_name="index_valuation",
                estimated_rows=row_estimates.get("index_valuation"),
            ),
            self._get_table_snapshot(
                MacroIndicatorModel,
                source_name="宏观经济指标",
                table_name="macro_indicator",
                date_column="period",
                estimated_rows=row_estimates.get("macro_indicator"),
            ),
            self._get_table_snapshot(
                StockDailyCloseModel,
                source_name="个股日线行情",
                table_name="stock_daily_close",
                estimated_rows=row_estimates.get("stock_daily_close"),
            ),
            self._get_table_snapshot(
                IndustryDailyBarModel,
                source_name="行业日线行情",
                table_name="industry_daily_bar",
                estimated_rows=row_estimates.get("industry_daily_bar"),
            ),
            self._get_table_snapshot(
                IndustryMembershipEventModel,
                source_name="申万行业成分",
                table_name="industry_membership_event",
                date_column=None,
                ingested_column="fetched_at",
                estimated_rows=row_estimates.get("industry_membership_event"),
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
            active_index_count=active_index_count,
            latest_trade_date=latest_trade_date,
            data_sources=data_sources,
            recent_runs=recent_runs,
            db_connected=True,
        )
