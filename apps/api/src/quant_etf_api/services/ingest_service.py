from __future__ import annotations

import logging
import math
import threading
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.akshare_fund import AkShareFundClient
from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.clients.akshare_macro import AkShareMacroClient
from quant_etf_api.infra.trading_calendar import TradingCalendar
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    EtfDailyBarModel,
    EtfDailyShareModel,
    IndexDailyBarModel,
    IndexValuationModel,
    MacroIndicatorModel,
)
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.etf_daily_bar import EtfDailyBarRepository
from quant_etf_api.infra.db.repositories.etf_daily_share import EtfDailyShareRepository
from quant_etf_api.infra.db.repositories.etf_universe import EtfUniverseRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.db.repositories.macro_indicator import MacroIndicatorRepository
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.schemas.market_data import (
    BenchmarkIndex,
    DailyBar,
    IndexSummary,
    IndexValuation,
    MacroIndicatorSchema,
    ShareSnapshot,
)
from quant_etf_api.services.run_service import RunService

logger = logging.getLogger(__name__)

# 防止 run_daily_ingest 被调度器、手动按钮、重试同时触发
_daily_ingest_lock = threading.Lock()


def _clean_price(value: Any) -> float | None:
    """将 NaN 价格清洗为 None，避免 PostgreSQL float 列存 NaN 污染收益链。

    Args:
        value: 上游返回的价格或成交量数值。

    Returns:
        有限数值原样返回，NaN/None 转为 None。
    """
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except TypeError:
        pass
    return value


# ────────────────────────── ETF 行 → Schema ──────────────────────────


def _bar_row_to_schema(row: EtfDailyBarModel) -> DailyBar:
    return DailyBar(
        trade_date=row.trade_date,
        code=row.etf_code,
        open_price=row.open_price,
        high_price=row.high_price,
        low_price=row.low_price,
        close_price=row.close_price,
        change_pct=row.change_pct,
        volume=row.volume,
        turnover=row.turnover,
        source=row.source,
        ingested_at=row.ingested_at,
    )


def _share_row_to_schema(row: EtfDailyShareModel) -> ShareSnapshot:
    return ShareSnapshot(
        trade_date=row.trade_date,
        etf_code=row.etf_code,
        shares_total=row.shares_total,
        shares_delta=row.shares_delta,
        shares_delta_pct=row.shares_delta_pct,
        nav=row.nav,
        aum=row.aum,
        source=row.source,
        ingested_at=row.ingested_at,
    )


# ──────────────────────── 指数行 → Schema ────────────────────────────


def _index_bar_row_to_schema(row: IndexDailyBarModel) -> DailyBar:
    return DailyBar(
        trade_date=row.trade_date,
        code=row.index_code,
        open_price=row.open_price,
        high_price=row.high_price,
        low_price=row.low_price,
        close_price=row.close_price,
        change_pct=row.change_pct,
        volume=row.volume,
        turnover=row.turnover,
        source=row.source,
        ingested_at=row.ingested_at,
    )


def _index_valuation_row_to_schema(row: IndexValuationModel) -> IndexValuation:
    return IndexValuation(
        trade_date=row.trade_date,
        index_code=row.index_code,
        pe=row.pe,
        pe_percentile=row.pe_percentile,
        pb=row.pb,
        pb_percentile=row.pb_percentile,
        dividend_yield=row.dividend_yield,
        source=row.source,
    )


def _macro_row_to_schema(row: MacroIndicatorModel) -> MacroIndicatorSchema:
    return MacroIndicatorSchema(
        indicator_code=row.indicator_code,
        indicator_name=row.indicator_name,
        period=row.period,
        value=row.value,
        unit=row.unit,
        source=row.source,
    )


# ──────────────────────────── IngestService ──────────────────────────


class IngestService:
    """数据摄取服务。

    提供 ETF 行情 / 份额 / 指数日线 / 指数估值 / 宏观指标的数据拉取、
    幂等写入和读穿透缓存，供 API 路由和定时调度器使用。
    读取统一走仓库；运行记录生命周期委托 RunService；
    摄取完成后自动执行数据质量检测（data_quality 接入数据闭环）。
    """

    def __init__(
        self,
        db: Session,
        universe_repo: EtfUniverseRepository | None = None,
        run_svc: RunService | None = None,
        run_repo: ResearchRunRepository | None = None,
    ) -> None:
        """初始化数据摄取服务。

        Args:
            db: SQLAlchemy 同步 Session。
            universe_repo: ETF 池仓库，未提供时自动创建。
            run_svc: 运行记录服务（生命周期状态流转），未提供时自动创建。
            run_repo: 运行记录仓库（子项明细写入），未提供时自动创建。
        """
        self._db = db
        self._universe_repo = universe_repo or EtfUniverseRepository(db)
        self._run_repo = run_repo or ResearchRunRepository(db)
        self._run_svc = run_svc or RunService(db, run_repo=self._run_repo)
        self._etf_bar_repo = EtfDailyBarRepository(db)
        self._share_repo = EtfDailyShareRepository(db)
        self._index_bar_repo = IndexDailyBarRepository(db)
        self._valuation_repo = IndexValuationRepository(db)
        self._macro_repo = MacroIndicatorRepository(db)
        self._index_repo = BenchmarkIndexRepository(db)

    def _enqueue_data_fill(self, resource: str, code: str | None = None) -> None:
        """查询未命中时入队后台补数任务，不在请求线程同步抓取。

        相同资源在 pending/running 状态下通过 job_key 幂等去重，
        避免并发 GET 重复触发同一资源的抓取。

        Args:
            resource: 资源类型：bars/shares/index_bars/index_valuation/macro。
            code: 标的代码，macro 类型为空。
        """
        from quant_etf_api.infra.job_queue.queue import get_job_queue

        payload: dict = {"resource": resource}
        if code:
            payload["code"] = code
        job_key = f"{resource}:{code}" if code else resource
        get_job_queue().enqueue("data_fill", payload, job_key=job_key, max_attempts=2)

    def fill_resource(self, resource: str, code: str | None = None) -> int:
        """按资源类型执行后台补数（data_fill 处理器调用）。

        Args:
            resource: 资源类型：bars/shares/index_bars/index_valuation/macro。
            code: 标的代码，macro 类型为空。

        Returns:
            写入的记录数。

        Raises:
            ValueError: 未知的资源类型。
        """
        if resource == "bars":
            return self._fetch_and_upsert_bars_full_history(code or "")
        if resource == "shares":
            self._fetch_and_upsert_shares(code or "", date.today())
            return 1
        if resource == "index_bars":
            return self._fetch_and_upsert_index_bars(code or "")
        if resource == "index_valuation":
            return self._fetch_and_upsert_index_valuation(code or "")
        if resource == "macro":
            return self._fetch_and_upsert_macro()
        raise ValueError(f"未知补数资源类型: {resource}")

    def latest_trade_date(self) -> date:
        """获取最近交易日，通过交易日历而非简单返回今天。

        Returns:
            最近交易日日期。
        """
        return TradingCalendar().latest_trading_day()

    # ==================================================================
    # ETF 日线
    # ==================================================================

    def _fetch_and_upsert_bars_full_history(self, etf_code: str) -> int:
        """冷启动：一次性拉取 ETF 全量历史日线并幂等写入。

        使用新浪后端（fund_etf_hist_sina），单次调用返回从上市至今的全量数据，
        无需按年分批（约 425ms 完成，不受东方财富代理封锁影响）。
        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制。

        Returns:
            写入记录数
        """
        client = AkShareFundClient()
        try:
            bars = client.fetch_etf_daily_bars(etf_code)
        except Exception as exc:
            logger.warning("ETF %s 全量日线拉取失败: %s", etf_code, exc)
            return 0

        if not bars:
            return 0

        batch_size = 5000
        values = [
            {
                "trade_date": b.trade_date,
                "etf_code": etf_code,
                "open_price": b.open_price,
                "high_price": b.high_price,
                "low_price": b.low_price,
                "close_price": b.close_price,
                "volume": b.volume,
                "turnover": b.turnover,
                "change_pct": b.change_pct,
                "amplitude": b.amplitude,
                "source": "akshare",
                "ingested_at": utcnow(),
            }
            for b in bars
        ]
        for i in range(0, len(values), batch_size):
            batch = values[i : i + batch_size]
            stmt = (
                insert(EtfDailyBarModel)
                .values(batch)
                .on_conflict_do_nothing(constraint="uq_etf_daily_bar")
            )
            self._db.execute(stmt)
        self._db.commit()
        return len(bars)

    def _fetch_and_upsert_bars_incremental(self, etf_code: str) -> str:
        """增量补全 ETF 日线：仅拉取 DB 最新日期之后的缺失数据。

        若 DB 已有当日（或更新）数据则直接跳过 API 调用，避免重复拉取。
        若 DB 无任何历史数据则触发全量拉取。

        Returns:
            API 返回的最新交易日日期字符串；无数据时返回空字符串
        """
        today = date.today()
        max_date: date | None = self._etf_bar_repo.get_latest_date(etf_code)

        # DB 已有当日或更新的数据，无需拉取
        if max_date is not None and max_date >= today:
            return str(max_date)

        # 无历史数据，走全量拉取
        if max_date is None:
            self._fetch_and_upsert_bars_full_history(etf_code)
            refreshed: date | None = self._etf_bar_repo.get_latest_date(etf_code)
            return str(refreshed) if refreshed else ""

        # 有历史数据，拉取 max_date 到今日的缺口数据
        start_str = (max_date - timedelta(days=1)).strftime("%Y%m%d")
        end_str = today.strftime("%Y%m%d")
        bars = AkShareFundClient().fetch_etf_daily_bars(
            etf_code, start_date=start_str, end_date=end_str
        )
        if not bars:
            return str(max_date)
        stmt = (
            insert(EtfDailyBarModel)
            .values(
                [
                    {
                        "trade_date": b.trade_date,
                        "etf_code": etf_code,
                        "open_price": b.open_price,
                        "high_price": b.high_price,
                        "low_price": b.low_price,
                        "close_price": b.close_price,
                        "volume": b.volume,
                        "turnover": b.turnover,
                        "change_pct": b.change_pct,
                        "amplitude": b.amplitude,
                        "source": "akshare",
                        "ingested_at": utcnow(),
                    }
                    for b in bars
                ]
            )
            .on_conflict_do_nothing(constraint="uq_etf_daily_bar")
        )
        self._db.execute(stmt)
        self._db.commit()
        return str(bars[-1].trade_date)

    def _query_etf_bars(
        self,
        etf_code: str,
        limit: int,
        start_date: date | None,
        end_date: date | None,
    ) -> list[EtfDailyBarModel]:
        """构建 ETF 日线查询（日期范围模式或 limit 模式），读取走仓库。"""
        if start_date and end_date:
            return self._etf_bar_repo.find_by_code_date_range(etf_code, start_date, end_date)
        return self._etf_bar_repo.find_by_code_limit(etf_code, limit)

    def get_daily_bars(
        self,
        etf_code: str,
        limit: int = 250,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[DailyBar]:
        """ETF 日线读穿透缓存。

        提供 start_date/end_date 时使用日期范围查询，否则使用 limit。
        未命中时不再在请求线程同步抓取外部 API，而是入队后台补数任务，
        本次请求直接返回空列表，避免阻塞请求线程池。
        """
        try:
            rows = self._query_etf_bars(etf_code, limit, start_date, end_date)
            if rows:
                return [_bar_row_to_schema(r) for r in rows]
            self._enqueue_data_fill("bars", etf_code)
        except Exception:
            logger.warning("get_daily_bars failed for %s", etf_code, exc_info=True)
            self._db.rollback()
        return []

    # ==================================================================
    # ETF 份额
    # ==================================================================

    def _fetch_and_upsert_shares(self, etf_code: str, trade_date: date) -> None:
        """从 AkShare 拉取 ETF 份额快照并幂等写入 DB。

        写入后自动计算与前一日份额的 delta 和 delta_pct。
        """
        snapshot = AkShareFundClient().fetch_share_snapshot(etf_code)
        if snapshot is None:
            raise ValueError(f"无法获取 {etf_code} 的份额数据")
        stmt = (
            insert(EtfDailyShareModel)
            .values(
                [
                    {
                        "trade_date": trade_date,
                        "etf_code": etf_code,
                        "shares_total": snapshot.shares_total,
                        "aum": snapshot.aum,
                        "nav": round(snapshot.price, 3),
                        "source": "akshare",
                        "ingested_at": utcnow(),
                    }
                ]
            )
            .on_conflict_do_nothing(constraint="uq_etf_daily_share")
        )
        self._db.execute(stmt)
        self._db.commit()

        # 计算与前一日份额的差值
        prev = self._share_repo.find_latest_before(etf_code, trade_date)
        if prev is not None and prev.shares_total is not None and snapshot.shares_total > 0:
            delta = round(snapshot.shares_total - prev.shares_total, 2)
            delta_pct = round(delta / prev.shares_total * 100, 2) if prev.shares_total > 0 else None
            row = self._share_repo.find_by_code_date(etf_code, trade_date)
            if row is not None:
                row.shares_delta = delta
                row.shares_delta_pct = delta_pct
                self._db.commit()

    def _query_shares(
        self,
        etf_code: str,
        limit: int,
        start_date: date | None,
        end_date: date | None,
    ) -> list[EtfDailyShareModel]:
        """构建 ETF 份额查询（日期范围模式或 limit 模式），读取走仓库。"""
        if start_date and end_date:
            return self._share_repo.find_by_code_date_range(etf_code, start_date, end_date)
        return self._share_repo.find_by_code_limit(etf_code, limit)

    def get_share_history(
        self,
        etf_code: str,
        limit: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[ShareSnapshot]:
        """ETF 份额读穿透缓存。

        未命中时入队后台补数任务，不再在请求线程同步抓取外部 API。
        """
        try:
            rows = self._query_shares(etf_code, limit, start_date, end_date)
            if rows:
                return [_share_row_to_schema(r) for r in rows]
            self._enqueue_data_fill("shares", etf_code)
        except Exception:
            logger.warning("get_share_history failed for %s", etf_code, exc_info=True)
            self._db.rollback()
        return []

    # ==================================================================
    # 指数日线（AkShare）
    # ==================================================================

    def _fetch_and_upsert_index_bars(self, index_code: str, incremental: bool = True) -> int:
        """从 AkShare 拉取指数日线并幂等写入 index_daily_bar。

        增量模式（默认）：仅拉取 DB 中最新日期之后的数据；
        全量模式（冷启动）：拉取全量历史数据。

        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制。

        Returns:
            写入记录数
        """
        if incremental:
            # 查询该指数最新数据日期
            latest = self._index_bar_repo.get_latest_date(index_code)
            if latest is not None:
                # 已有数据，仅做增量（AkShare 客户端当前不支持增量，仍拉全量后过滤）
                pass

        bars = AkShareIndexClient().fetch_index_daily(index_code)
        if not bars:
            return 0

        # 增量模式：仅保留 DB 中不存在的记录
        if incremental:
            existing = latest
            if existing is not None:
                bars = [b for b in bars if b.trade_date > existing]

        batch_size = 5000
        values = [
            {
                "trade_date": b.trade_date,
                "index_code": index_code,
                "open_price": _clean_price(b.open_price),
                "high_price": _clean_price(b.high_price),
                "low_price": _clean_price(b.low_price),
                "close_price": _clean_price(b.close_price),
                "prev_close_price": _clean_price(b.prev_close_price),
                "change_pct": _clean_price(b.change_pct),
                "volume": _clean_price(b.volume),
                "turnover": _clean_price(b.turnover),
                "source": "akshare",
                "ingested_at": utcnow(),
            }
            for b in bars
        ]
        for i in range(0, len(values), batch_size):
            batch = values[i : i + batch_size]
            stmt = (
                insert(IndexDailyBarModel)
                .values(batch)
                .on_conflict_do_nothing(constraint="uq_index_daily_bar")
            )
            self._db.execute(stmt)
        self._db.commit()
        return len(bars)

    def get_benchmark_indexes(self) -> list[BenchmarkIndex]:
        """返回所有活跃的基准指数（从种子表读取，已停用的不返回）。"""
        rows = self._index_repo.find_active()
        return [BenchmarkIndex(index_code=r.index_code, index_name=r.name_cn) for r in rows]

    def get_index_summaries(self) -> list["IndexSummary"]:
        """返回所有活跃指数的汇总数据（最新行情 + 估值快照），单次查询。

        使用子查询分别取每个指数的最新 bar 和最新 valuation，
        通过 OUTER JOIN 关联，无数据时对应字段返回 None。
        不触发冷启动拉取 —— 仅查询 DB 已有数据。

        Returns:
            指数汇总列表，按 index_code 升序排列。
        """
        # 子查询：每个指数的最新 bar 日期
        latest_bar_dates = (
            self._db.query(
                IndexDailyBarModel.index_code,
                func.max(IndexDailyBarModel.trade_date).label("max_bar_date"),
            )
            .group_by(IndexDailyBarModel.index_code)
            .subquery("latest_bar_dates")
        )

        # 子查询：每个指数的最新估值日期
        latest_val_dates = (
            self._db.query(
                IndexValuationModel.index_code,
                func.max(IndexValuationModel.trade_date).label("max_val_date"),
            )
            .group_by(IndexValuationModel.index_code)
            .subquery("latest_val_dates")
        )

        rows = (
            self._db.query(
                BenchmarkIndexModel.index_code,
                BenchmarkIndexModel.name_cn,
                IndexDailyBarModel.close_price,
                IndexDailyBarModel.change_pct,
                IndexDailyBarModel.trade_date.label("bar_date"),
                IndexValuationModel.pe,
                IndexValuationModel.pe_percentile,
                IndexValuationModel.pb,
                IndexValuationModel.pb_percentile,
                IndexValuationModel.dividend_yield,
                IndexValuationModel.trade_date.label("valuation_date"),
            )
            .filter(BenchmarkIndexModel.is_active.is_(True))
            .outerjoin(
                latest_bar_dates,
                BenchmarkIndexModel.index_code == latest_bar_dates.c.index_code,
            )
            .outerjoin(
                IndexDailyBarModel,
                (IndexDailyBarModel.index_code == latest_bar_dates.c.index_code)
                & (IndexDailyBarModel.trade_date == latest_bar_dates.c.max_bar_date),
            )
            .outerjoin(
                latest_val_dates,
                BenchmarkIndexModel.index_code == latest_val_dates.c.index_code,
            )
            .outerjoin(
                IndexValuationModel,
                (IndexValuationModel.index_code == latest_val_dates.c.index_code)
                & (IndexValuationModel.trade_date == latest_val_dates.c.max_val_date),
            )
            .order_by(BenchmarkIndexModel.index_code)
            .all()
        )

        return [
            IndexSummary(
                index_code=r.index_code,
                index_name=r.name_cn,
                close_price=float(r.close_price) if r.close_price is not None else None,
                change_pct=float(r.change_pct) if r.change_pct is not None else None,
                bar_date=r.bar_date,
                pe=float(r.pe) if r.pe is not None else None,
                pe_percentile=float(r.pe_percentile) if r.pe_percentile is not None else None,
                pb=float(r.pb) if r.pb is not None else None,
                pb_percentile=float(r.pb_percentile) if r.pb_percentile is not None else None,
                dividend_yield=float(r.dividend_yield) if r.dividend_yield is not None else None,
                valuation_date=r.valuation_date,
            )
            for r in rows
        ]

    def _query_index_bars(
        self,
        index_code: str,
        limit: int,
        start_date: date | None,
        end_date: date | None,
    ) -> list[IndexDailyBarModel]:
        """构建指数日线查询（日期范围模式或 limit 模式），读取走仓库。"""
        if start_date and end_date:
            return self._index_bar_repo.find_by_code_date_range(index_code, start_date, end_date)
        return self._index_bar_repo.find_by_code_limit(index_code, limit)

    def get_index_daily_bars(
        self,
        index_code: str,
        limit: int = 250,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[DailyBar]:
        """指数日线读穿透缓存。

        未命中时入队后台补数任务，不再在请求线程同步抓取外部 API。
        """
        try:
            rows = self._query_index_bars(index_code, limit, start_date, end_date)
            if rows:
                return [_index_bar_row_to_schema(r) for r in rows]
            self._enqueue_data_fill("index_bars", index_code)
        except Exception:
            logger.warning(
                "get_index_daily_bars failed for %s, returning []", index_code, exc_info=True
            )
            self._db.rollback()

        return []

    # ==================================================================
    # 指数估值 PE/PB（AkShare）
    # ==================================================================

    def _fetch_and_upsert_index_valuation(self, index_code: str) -> int:
        """从 AkShare 拉取指数 PE/PB 估值并幂等写入 index_valuation。

        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制
        （每行 9 字段，批次上限 7000 行 = 63000 参数）。

        Returns:
            写入记录数
        """
        valuations = AkShareIndexClient().fetch_index_valuation(index_code)
        if not valuations:
            return 0

        batch_size = 7000
        values = [
            {
                "trade_date": v.trade_date,
                "index_code": index_code,
                "pe": v.pe,
                "pe_percentile": v.pe_percentile,
                "pb": v.pb,
                "pb_percentile": v.pb_percentile,
                "dividend_yield": v.dividend_yield,
                "source": v.source,
                "ingested_at": utcnow(),
            }
            for v in valuations
        ]
        for i in range(0, len(values), batch_size):
            batch = values[i : i + batch_size]
            stmt = (
                insert(IndexValuationModel)
                .values(batch)
                .on_conflict_do_nothing(constraint="uq_index_valuation")
            )
            self._db.execute(stmt)
        self._db.commit()
        return len(valuations)

    def _query_index_valuation(
        self,
        index_code: str,
        limit: int,
        start_date: date | None,
        end_date: date | None,
    ) -> list[IndexValuationModel]:
        """构建指数估值查询（日期范围模式或 limit 模式），读取走仓库。"""
        if start_date and end_date:
            return self._valuation_repo.find_by_code_date_range(index_code, start_date, end_date)
        return self._valuation_repo.find_by_code_limit(index_code, limit)

    def get_index_valuation(
        self,
        index_code: str,
        limit: int = 30,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[IndexValuation]:
        """指数估值读穿透缓存。

        提供 start_date/end_date 时使用日期范围查询，否则使用 limit。
        未命中时入队后台补数任务，不再在请求线程同步抓取外部 API。
        """
        try:
            rows = self._query_index_valuation(index_code, limit, start_date, end_date)
            if rows:
                return [_index_valuation_row_to_schema(r) for r in rows]
            self._enqueue_data_fill("index_valuation", index_code)
        except Exception:
            logger.warning(
                "get_index_valuation failed for %s, returning []", index_code, exc_info=True
            )
            self._db.rollback()

        return []

    # ==================================================================
    # 日期范围元数据
    # ==================================================================

    def get_etf_date_range(self, etf_code: str) -> tuple[date | None, date | None]:
        """查询 ETF 日线数据的最早和最晚日期（读取走仓库）。"""
        return self._etf_bar_repo.get_date_range(etf_code)

    def get_index_date_range(self, index_code: str) -> tuple[date | None, date | None]:
        """查询指数日线数据的最早和最晚日期（读取走仓库）。"""
        return self._index_bar_repo.get_date_range(index_code)

    # ==================================================================
    # 宏观指标（AkShare）
    # ==================================================================

    def _fetch_and_upsert_macro(self) -> int:
        """从 AkShare 拉取所有宏观指标（CPI/PMI/LPR）并幂等写入 macro_indicator。

        Returns:
            写入记录数
        """
        indicators = AkShareMacroClient().fetch_all()
        if not indicators:
            return 0
        stmt = (
            insert(MacroIndicatorModel)
            .values(
                [
                    {
                        "indicator_code": i.indicator_code,
                        "indicator_name": i.indicator_name,
                        "period": i.period,
                        "value": i.value,
                        "unit": i.unit,
                        "source": "akshare",
                        "period_date": i.period_date,
                        "ingested_at": utcnow(),
                    }
                    for i in indicators
                ]
            )
            .on_conflict_do_nothing(constraint="uq_macro_indicator")
        )
        self._db.execute(stmt)
        self._db.commit()
        return len(indicators)

    def get_macro_indicators(
        self, indicator_code: str, limit: int = 60
    ) -> list[MacroIndicatorSchema]:
        """宏观指标读穿透缓存。

        未命中时入队后台补数任务，不再在请求线程同步抓取外部 API。
        """
        try:
            rows = self._macro_repo.find_by_code_limit(indicator_code, limit)
            if rows:
                return [_macro_row_to_schema(r) for r in reversed(rows)]
            self._enqueue_data_fill("macro")
        except Exception:
            logger.warning(
                "get_macro_indicators failed for %s, returning []", indicator_code, exc_info=True
            )
            self._db.rollback()

        return []

    # ==================================================================
    # 数据质量检查
    # ==================================================================

    def check_data_freshness(self) -> dict[str, Any]:
        """检查各数据表的新鲜度和覆盖率。

        针对每个活跃 ETF / 基准指数，检查对应数据表中是否有记录、
        最新数据日期距今是否超过 3 个自然日（节假日容忍），返回汇总结果。
        """
        today = date.today()
        cal = TradingCalendar()
        # 使用最近交易日作为新鲜度基准，容忍 1 个交易日间隔
        latest_td = cal.latest_trading_day(today)
        stale_threshold = latest_td - timedelta(days=1)
        result: dict = {}

        etfs = self._universe_repo.find_all_active()

        # --- ETF 日线 ---
        bar_stale = []
        bar_missing = []
        bar_latest: date | None = None
        for etf in etfs:
            max_d = self._etf_bar_repo.get_latest_date(etf.etf_code)
            if max_d is None:
                bar_missing.append(
                    {
                        "code": etf.etf_code,
                        "name": etf.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
            else:
                if bar_latest is None or max_d > bar_latest:
                    bar_latest = max_d
                if max_d < stale_threshold:
                    bar_stale.append(
                        {
                            "code": etf.etf_code,
                            "name": etf.name_cn,
                            "latest_date": str(max_d),
                            "is_stale": True,
                        }
                    )

        result["etf_bars"] = {
            "total": len(etfs),
            "up_to_date": len(etfs) - len(bar_stale) - len(bar_missing),
            "stale": bar_stale,
            "missing": bar_missing,
            "latest_date": str(bar_latest) if bar_latest else None,
        }

        # --- ETF 份额 ---
        share_stale = []
        share_missing = []
        share_latest: date | None = None
        for etf in etfs:
            max_d = self._share_repo.get_latest_date(etf.etf_code)
            if max_d is None:
                share_missing.append(
                    {
                        "code": etf.etf_code,
                        "name": etf.name_cn,
                        "latest_date": None,
                        "is_stale": True,
                    }
                )
            else:
                if share_latest is None or max_d > share_latest:
                    share_latest = max_d
                if max_d < stale_threshold:
                    share_stale.append(
                        {
                            "code": etf.etf_code,
                            "name": etf.name_cn,
                            "latest_date": str(max_d),
                            "is_stale": True,
                        }
                    )

        result["etf_shares"] = {
            "total": len(etfs),
            "up_to_date": len(etfs) - len(share_stale) - len(share_missing),
            "stale": share_stale,
            "missing": share_missing,
            "latest_date": str(share_latest) if share_latest else None,
        }

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

        # --- 字段级质量 ---
        total_etf_bars = self._db.query(func.count(EtfDailyBarModel.id)).scalar() or 0
        null_etf_change_pct = (
            self._db.query(func.count(EtfDailyBarModel.id))
            .filter(EtfDailyBarModel.change_pct.is_(None))
            .scalar()
            or 0
        )
        total_index_bars = self._db.query(func.count(IndexDailyBarModel.id)).scalar() or 0
        null_index_change_pct = (
            self._db.query(func.count(IndexDailyBarModel.id))
            .filter(IndexDailyBarModel.change_pct.is_(None))
            .scalar()
            or 0
        )
        result["field_quality"] = {
            "etf_bars": {
                "total_records": total_etf_bars,
                "change_pct_null": null_etf_change_pct,
                "change_pct_null_rate": round(null_etf_change_pct / total_etf_bars, 4)
                if total_etf_bars
                else 0,
            },
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

    # ==================================================================
    # 共享私有方法
    # ==================================================================

    def _process_etf_ingest_batch(
        self, run_id: str, etfs: list, today: date
    ) -> tuple[int, int, int]:
        """遍历所有活跃 ETF，逐个拉取日线和份额数据并写入运行子项记录。

        两个调用方（run_daily_ingest、refresh_etf_data）共享此方法，
        避免 ETF 遍历逻辑重复。

        Args:
            run_id: 运行记录 ID。
            etfs: 活跃 ETF 列表（EtfUniverseModel 实例）。
            today: 当前日期。

        Returns:
            (success_count, failed_count, skipped_count)
        """
        etf_success = 0
        etf_failed = 0
        etf_skipped = 0

        if not etfs:
            return etf_success, etf_failed, etf_skipped

        for etf in etfs:
            etf_code = etf.etf_code
            item_status = "success"
            item_message = ""

            try:
                latest_bar_date = self._fetch_and_upsert_bars_incremental(etf_code)
                if not latest_bar_date:
                    item_status = "skipped"
                    item_message = "未获取到 K 线数据"
                    etf_skipped += 1
                elif latest_bar_date == str(today):
                    try:
                        self._fetch_and_upsert_shares(etf_code, today)
                    except Exception:
                        item_message = "份额数据拉取失败"
                    etf_success += 1
                else:
                    item_status = "skipped"
                    item_message = f"非交易日，最新行情日期为 {latest_bar_date}"
                    etf_skipped += 1
            except Exception as e:
                item_status = "failed"
                item_message = str(e)[:500]
                etf_failed += 1
                self._db.rollback()
                logger.warning("ETF %s 数据摄取失败: %s", etf_code, e)

            try:
                self._run_repo.add_item(
                    run_id=run_id,
                    etf_code=etf_code,
                    status=item_status,
                    message=item_message or None,
                )
            except Exception:
                self._db.rollback()
                logger.warning("写入 ResearchRunItem 失败: %s", etf_code, exc_info=True)

        return etf_success, etf_failed, etf_skipped

    # ==================================================================
    # 全量日频摄取（后台线程入口）
    # ==================================================================

    def _try_acquire_ingest_lock(self, run_id: str) -> bool:
        """尝试获取摄取互斥锁，失败时将运行记录标记为 skipped。

        daily_ingest 与三个手动刷新入口共享同一把进程内互斥锁，
        保证同一时刻只有一条摄取流水线在写数据。

        Args:
            run_id: 运行记录 ID。

        Returns:
            True 表示成功获取锁，可继续执行；False 表示已有摄取任务在运行。
        """
        if _daily_ingest_lock.acquire(blocking=False):
            return True
        self._run_svc.mark_skipped(
            run_id,
            metrics={
                "reason": "concurrent_skip",
                "message": "另一个摄取任务正在运行，跳过本次执行",
            },
        )
        return False

    def _run_quality_checks(self, trade_date: date) -> dict[str, Any]:
        """对当日摄取结果执行数据质量检测并返回统计（接入数据闭环）。

        检测项：日线异常（涨跌幅/零量/收盘价）、估值异常（负值/百分位越界）、
        连续性缺口。异常仅记录日志并计入运行指标，不中断摄取流程。

        Args:
            trade_date: 需要检测的交易日。

        Returns:
            quality 统计字典：{scope: 异常数量}。
        """
        from quant_etf_api.services.data_quality import (
            check_continuity,
            check_daily_bar_anomalies,
            check_valuation_anomalies,
        )

        stats: dict[str, Any] = {}
        try:
            etf_bars = self._etf_bar_repo.find_by_date_range(trade_date, trade_date)
            stats["etf_bar_anomalies"] = len(check_daily_bar_anomalies(etf_bars))
            stats["etf_bar_gaps"] = len(check_continuity(etf_bars))
        except Exception:
            logger.warning("ETF 日线质量检测失败", exc_info=True)
            stats["etf_bar_anomalies"] = 0
            stats["etf_bar_gaps"] = 0

        try:
            index_bars = self._index_bar_repo.find_by_date_range(trade_date, trade_date)
            stats["index_bar_anomalies"] = len(check_daily_bar_anomalies(index_bars))
            stats["index_bar_gaps"] = len(check_continuity(index_bars))
        except Exception:
            logger.warning("指数日线质量检测失败", exc_info=True)
            stats["index_bar_anomalies"] = 0
            stats["index_bar_gaps"] = 0

        try:
            valuation_rows = self._valuation_repo.find_by_date_range(trade_date, trade_date)
            stats["valuation_anomalies"] = len(check_valuation_anomalies(valuation_rows))
        except Exception:
            logger.warning("估值质量检测失败", exc_info=True)
            stats["valuation_anomalies"] = 0

        logger.info("数据质量检测完成: trade_date=%s stats=%s", trade_date, stats)
        return stats

    def run_daily_ingest(self, run_id: str) -> None:
        """执行日频数据全量摄取任务（后台线程入口）。

        依次拉取：
        1. 所有活跃 ETF 的日线和份额
        2. 所有基准指数的日线和估值
        3. 宏观指标（CPI/PMI/LPR）

        完成后更新 research_run 状态为 success/skipped/failed
        （并发冲突或非交易日时标记为 skipped，语义上表示"未执行"）。
        """
        # 非阻塞并发控制：如果已有 ingest 正在运行，跳过本次执行
        if not self._try_acquire_ingest_lock(run_id):
            return
        try:
            start_time = utcnow()
            self._run_svc.mark_running(run_id)

            today = date.today()
            cal = TradingCalendar()

            if not cal.is_trading_day(today):
                self._run_svc.mark_skipped(
                    run_id,
                    metrics={"reason": "holiday", "message": "非交易日，跳过数据摄取"},
                )
                return

            # ------------------------------ 1. ETF 行情 + 份额 ------------------------------
            etfs = self._universe_repo.find_all_active()
            etf_success, etf_failed, etf_skipped = self._process_etf_ingest_batch(
                run_id, etfs, today
            )

            # ------------------------------ 2. 指数日线 + 估值 ------------------------------
            indexes = (
                self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()
            )
            index_bar_count = 0
            index_valuation_count = 0

            for idx in indexes:
                try:
                    index_bar_count += self._fetch_and_upsert_index_bars(idx.index_code)
                except Exception as e:
                    logger.warning("指数 %s 日线拉取失败: %s", idx.index_code, e)

                try:
                    index_valuation_count += self._fetch_and_upsert_index_valuation(idx.index_code)
                except Exception as e:
                    logger.warning("指数 %s 估值拉取失败: %s", idx.index_code, e)

            # ------------------------------ 3. 宏观指标 ------------------------------
            macro_count = 0
            try:
                macro_count = self._fetch_and_upsert_macro()
            except Exception as e:
                logger.warning("宏观指标拉取失败: %s", e)

            # 数据质量检测接入摄取闭环：异常记录日志并计入运行指标
            quality = self._run_quality_checks(today)

            # ------------------------------ 汇总 ------------------------------
            self._run_svc.mark_success(
                run_id,
                metrics={
                    "etf": {
                        "total": len(etfs),
                        "success": etf_success,
                        "failed": etf_failed,
                        "skipped": etf_skipped,
                    },
                    "index": {
                        "total": len(indexes),
                        "bar_records": index_bar_count,
                        "valuation_records": index_valuation_count,
                    },
                    "macro": {"records": macro_count},
                    "quality": quality,
                    "duration_seconds": round((utcnow() - start_time).total_seconds(), 1),
                },
            )

        except Exception as e:
            self._db.rollback()
            logger.warning("run_daily_ingest 整体失败: %s", e, exc_info=True)
            self._run_svc.mark_failed(run_id, str(e)[:1000])
        finally:
            _daily_ingest_lock.release()

    # ==================================================================
    # 按类型拆分的数据刷新（后台线程入口）
    # ==================================================================

    def refresh_etf_data(self, run_id: str) -> None:
        """刷新所有活跃 ETF 的日线和份额数据（后台线程入口）。

        仅处理 ETF 相关数据，不涉及指数和宏观。

        Args:
            run_id: 运行记录 ID。
        """
        if not self._try_acquire_ingest_lock(run_id):
            return
        start_time = utcnow()
        try:
            self._run_svc.mark_running(run_id)

            today = date.today()
            cal = TradingCalendar()
            if not cal.is_trading_day(today):
                self._run_svc.mark_skipped(
                    run_id,
                    metrics={"reason": "holiday", "message": "非交易日，跳过数据摄取"},
                )
                return

            etfs = self._universe_repo.find_all_active()
            etf_success, etf_failed, etf_skipped = self._process_etf_ingest_batch(
                run_id, etfs, today
            )

            quality = self._run_quality_checks(today)
            self._run_svc.mark_success(
                run_id,
                metrics={
                    "etf": {
                        "total": len(etfs),
                        "success": etf_success,
                        "failed": etf_failed,
                        "skipped": etf_skipped,
                    },
                    "quality": quality,
                    "duration_seconds": round((utcnow() - start_time).total_seconds(), 1),
                },
            )

        except Exception as e:
            self._db.rollback()
            logger.warning("refresh_etf_data 整体失败: %s", e, exc_info=True)
            self._run_svc.mark_failed(run_id, str(e)[:1000])
        finally:
            _daily_ingest_lock.release()

    def refresh_index_data(self, run_id: str) -> None:
        """刷新所有基准指数的日线和估值数据（后台线程入口）。

        Args:
            run_id: 运行记录 ID。
        """
        if not self._try_acquire_ingest_lock(run_id):
            return
        start_time = utcnow()
        try:
            self._run_svc.mark_running(run_id)

            today = date.today()
            cal = TradingCalendar()
            if not cal.is_trading_day(today):
                self._run_svc.mark_skipped(
                    run_id,
                    metrics={"reason": "holiday", "message": "非交易日，跳过数据摄取"},
                )
                return

            indexes = self._index_repo.find_all()
            index_bar_count = 0
            index_valuation_count = 0

            for idx in indexes:
                try:
                    index_bar_count += self._fetch_and_upsert_index_bars(idx.index_code)
                except Exception as e:
                    logger.warning("指数 %s 日线拉取失败: %s", idx.index_code, e)

                try:
                    index_valuation_count += self._fetch_and_upsert_index_valuation(idx.index_code)
                except Exception as e:
                    logger.warning("指数 %s 估值拉取失败: %s", idx.index_code, e)

            quality = self._run_quality_checks(today)
            self._run_svc.mark_success(
                run_id,
                metrics={
                    "index": {
                        "total": len(indexes),
                        "bar_records": index_bar_count,
                        "valuation_records": index_valuation_count,
                    },
                    "quality": quality,
                    "duration_seconds": round((utcnow() - start_time).total_seconds(), 1),
                },
            )

        except Exception as e:
            self._db.rollback()
            logger.warning("refresh_index_data 整体失败: %s", e, exc_info=True)
            self._run_svc.mark_failed(run_id, str(e)[:1000])
        finally:
            _daily_ingest_lock.release()

    def refresh_macro_data(self, run_id: str) -> None:
        """刷新宏观指标数据（后台线程入口）。

        拉取 CPI、PMI、LPR 等宏观指标。

        Args:
            run_id: 运行记录 ID。
        """
        if not self._try_acquire_ingest_lock(run_id):
            return
        start_time = utcnow()
        try:
            self._run_svc.mark_running(run_id)

            today = date.today()
            cal = TradingCalendar()
            if not cal.is_trading_day(today):
                self._run_svc.mark_skipped(
                    run_id,
                    metrics={"reason": "holiday", "message": "非交易日，跳过数据摄取"},
                )
                return

            macro_count = self._fetch_and_upsert_macro()

            self._run_svc.mark_success(
                run_id,
                metrics={
                    "macro": {"records": macro_count},
                    "duration_seconds": round((utcnow() - start_time).total_seconds(), 1),
                },
            )

        except Exception as e:
            self._db.rollback()
            logger.warning("refresh_macro_data 整体失败: %s", e, exc_info=True)
            self._run_svc.mark_failed(run_id, str(e)[:1000])
        finally:
            _daily_ingest_lock.release()

    def run_cold_start(self, run_id: str) -> None:
        """冷启动：拉取全部 ETF 和指数的全量历史数据（后台线程入口）。

        与 run_daily_ingest 的区别：
        - ETF，指数日线拉取全量历史
        - 不拉取份额快照（份额为点状数据，无历史含义）
        - 跳过周末检查（冷启动可随时执行）
        - 指数日线/估值/宏观复用现有全量方法
        """
        start_time = utcnow()
        try:
            self._run_svc.mark_running(run_id)

            # 1. 全量 ETF 历史日线
            etfs = self._universe_repo.find_all_active()

            etf_bar_count = 0
            etf_failed_count = 0

            for etf in etfs:
                etf_code = etf.etf_code
                try:
                    n = self._fetch_and_upsert_bars_full_history(etf_code)
                    etf_bar_count += n
                    self._run_repo.add_item(
                        run_id=run_id,
                        etf_code=etf_code,
                        status="success",
                        message=f"写入 {n} 条日线",
                    )
                except Exception as e:
                    etf_failed_count += 1
                    self._db.rollback()
                    logger.warning("ETF %s 全量日线拉取失败: %s", etf_code, e)
                try:
                    self._db.commit()
                except Exception:
                    self._db.rollback()

            # 2. 指数全量日线 + 估值
            indexes = (
                self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()
            )
            index_bar_count = 0
            index_valuation_count = 0

            for idx in indexes:
                try:
                    index_bar_count += self._fetch_and_upsert_index_bars(
                        idx.index_code, incremental=False
                    )
                except Exception as e:
                    logger.warning("指数 %s 日线拉取失败: %s", idx.index_code, e)

                try:
                    index_valuation_count += self._fetch_and_upsert_index_valuation(idx.index_code)
                except Exception as e:
                    logger.warning("指数 %s 估值拉取失败: %s", idx.index_code, e)

            # 3. 宏观指标
            macro_count = 0
            try:
                macro_count = self._fetch_and_upsert_macro()
            except Exception as e:
                logger.warning("宏观指标拉取失败: %s", e)

            # 汇总
            self._run_svc.mark_success(
                run_id,
                metrics={
                    "etf": {
                        "total": len(etfs),
                        "bar_records": etf_bar_count,
                        "failed": etf_failed_count,
                    },
                    "index": {
                        "total": len(indexes),
                        "bar_records": index_bar_count,
                        "valuation_records": index_valuation_count,
                    },
                    "macro": {"records": macro_count},
                    "duration_seconds": round((utcnow() - start_time).total_seconds(), 1),
                },
            )

        except Exception as e:
            self._db.rollback()
            logger.warning("run_cold_start 整体失败: %s", e, exc_info=True)
            self._run_svc.mark_failed(run_id, str(e)[:1000])

    def run_startup_fill(self, run_id: str) -> None:
        """启动补全：检查所有 ETF 和指数的数据缺口并按需补全（系统启动时自动调用）。

        与 run_cold_start 的区别：
        - ETF 日线使用增量方法，已有当日数据的 ETF 直接跳过，避免重复 API 调用
        - 指数/宏观数据仅在超过 5 天未更新时才重新拉取
        """
        start_time = utcnow()
        stale_threshold = date.today() - timedelta(days=5)

        try:
            self._run_svc.mark_running(run_id)

            # 1. ETF 日线增量补全（已是最新则跳过）
            etfs = self._universe_repo.find_all_active()
            etf_filled = 0
            etf_skipped = 0
            etf_failed = 0

            for etf in etfs:
                etf_code = etf.etf_code
                try:
                    latest = self._fetch_and_upsert_bars_incremental(etf_code)
                    if latest == str(date.today()):
                        etf_skipped += 1
                    else:
                        etf_filled += 1
                except Exception as e:
                    etf_failed += 1
                    self._db.rollback()
                    logger.warning("ETF %s 启动补全失败: %s", etf_code, e)

            # 2. 指数日线 + 估值（超过 5 天未更新才重新拉取）
            indexes = self._index_repo.find_all()
            index_bar_count = 0
            index_val_count = 0

            for idx in indexes:
                idx_max = self._index_bar_repo.get_latest_date(idx.index_code)
                if idx_max is None or idx_max <= stale_threshold:
                    try:
                        index_bar_count += self._fetch_and_upsert_index_bars(idx.index_code)
                    except Exception as e:
                        logger.warning("指数 %s 日线补全失败: %s", idx.index_code, e)

                    try:
                        index_val_count += self._fetch_and_upsert_index_valuation(idx.index_code)
                    except Exception as e:
                        logger.warning("指数 %s 估值补全失败: %s", idx.index_code, e)

            # 3. 宏观指标（超过 5 天未入库才重新拉取）
            macro_latest_ingest: datetime | None = self._macro_repo.find_latest_ingested_at()
            # DateTime 列返回 naive datetime，去掉 tzinfo 后再比较
            macro_stale_threshold = utcnow() - timedelta(days=5)
            macro_count = 0
            if macro_latest_ingest is None or macro_latest_ingest < macro_stale_threshold:
                try:
                    macro_count = self._fetch_and_upsert_macro()
                except Exception as e:
                    logger.warning("宏观指标补全失败: %s", e)

            self._run_svc.mark_success(
                run_id,
                metrics={
                    "etf": {
                        "total": len(etfs),
                        "filled": etf_filled,
                        "skipped": etf_skipped,
                        "failed": etf_failed,
                    },
                    "index": {
                        "bar_records": index_bar_count,
                        "valuation_records": index_val_count,
                    },
                    "macro": {"records": macro_count},
                    "duration_seconds": round((utcnow() - start_time).total_seconds(), 1),
                },
            )

        except Exception as e:
            self._db.rollback()
            logger.warning("run_startup_fill 整体失败: %s", e, exc_info=True)
            self._run_svc.mark_failed(run_id, str(e)[:1000])
