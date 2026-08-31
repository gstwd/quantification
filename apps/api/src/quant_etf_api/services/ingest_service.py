from __future__ import annotations

import logging
import math
import threading
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.clients.akshare_macro import AkShareMacroClient
from quant_etf_api.infra.trading_calendar import TradingCalendar
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    IndexDailyBarModel,
    IndexValuationModel,
    MacroIndicatorModel,
)
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.db.repositories.macro_indicator import MacroIndicatorRepository
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.schemas.market_data import (
    BarQuality,
    BenchmarkIndex,
    DailyBar,
    IndexDataQuality,
    IndexSummary,
    IndexValuation,
    MacroIndicatorSchema,
    ValuationQuality,
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

    提供指数日线 / 指数估值 / 宏观指标的数据拉取、
    幂等写入和读穿透缓存，供 API 路由和定时调度器使用。
    读取统一走仓库；运行记录生命周期委托 RunService；
    摄取完成后自动执行数据质量检测（data_quality 接入数据闭环）。
    """

    def __init__(
        self,
        db: Session,
        run_svc: RunService | None = None,
        run_repo: ResearchRunRepository | None = None,
    ) -> None:
        """初始化数据摄取服务。

        Args:
            db: SQLAlchemy 同步 Session。
            run_svc: 运行记录服务（生命周期状态流转），未提供时自动创建。
            run_repo: 运行记录仓库（子项明细写入），未提供时自动创建。
        """
        self._db = db
        self._run_repo = run_repo or ResearchRunRepository(db)
        self._run_svc = run_svc or RunService(db, run_repo=self._run_repo)
        self._index_bar_repo = IndexDailyBarRepository(db)
        self._valuation_repo = IndexValuationRepository(db)
        self._macro_repo = MacroIndicatorRepository(db)
        self._index_repo = BenchmarkIndexRepository(db)

    def _enqueue_data_fill(self, resource: str, code: str | None = None) -> None:
        """查询未命中时入队后台补数任务，不在请求线程同步抓取。

        相同资源在 pending/running 状态下通过 job_key 幂等去重，
        避免并发 GET 重复触发同一资源的抓取。

        Args:
            resource: 资源类型：index_bars/index_valuation/macro。
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
            resource: 资源类型：index_bars/index_valuation/macro。
            code: 标的代码，macro 类型为空。

        Returns:
            写入的记录数。

        Raises:
            ValueError: 未知的资源类型。
        """
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
    # 指数日线（AkShare）
    # ==================================================================

    def _insert_index_bars(self, index_code: str, bars: list[Any]) -> int:
        """将日线数据批量幂等写入 index_daily_bar（不提交，由调用方统一提交）。

        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制。

        Args:
            index_code: 指数代码。
            bars: 待写入的日线数据列表（AkShare 客户端返回）。

        Returns:
            写入记录数。
        """
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
        return len(bars)

    def _fetch_and_upsert_index_bars(self, index_code: str, incremental: bool = True) -> int:
        """从 AkShare 拉取指数日线并幂等写入 index_daily_bar。

        增量模式（默认）：从 DB 最新日期回退缓冲窗口拉取，仅写入最新日期之后的数据；
        全量模式（冷启动）：拉取全量历史数据。

        Returns:
            写入记录数
        """
        latest = self._index_bar_repo.get_latest_date(index_code) if incremental else None
        if latest is not None:
            # 增量拉取：客户端从 latest 回退缓冲窗口，保证边界 bar 的涨跌幅可算
            bars = AkShareIndexClient().fetch_index_daily_since(index_code, latest)
        else:
            bars = AkShareIndexClient().fetch_index_daily(index_code)
        if not bars:
            return 0

        # 增量模式：仅保留 DB 中不存在的记录（同时丢弃缓冲窗口内的重复行）
        if latest is not None:
            bars = [b for b in bars if b.trade_date > latest]

        count = self._insert_index_bars(index_code, bars)
        self._db.commit()
        return count

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

    def _insert_index_valuations(self, index_code: str, valuations: list[Any]) -> int:
        """将估值数据批量幂等写入 index_valuation（不提交，由调用方统一提交）。

        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制
        （每行 9 字段，批次上限 7000 行 = 63000 参数）。

        Args:
            index_code: 指数代码。
            valuations: 待写入的估值数据列表（AkShare 客户端返回）。

        Returns:
            写入记录数。
        """
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
        return len(valuations)

    def _fetch_and_upsert_index_valuation(self, index_code: str) -> int:
        """从 AkShare 拉取指数 PE/PB 估值并幂等写入 index_valuation。

        Returns:
            写入记录数
        """
        valuations = AkShareIndexClient().fetch_index_valuation(index_code)
        if not valuations:
            return 0
        count = self._insert_index_valuations(index_code, valuations)
        self._db.commit()
        return count

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

    def get_index_date_range(self, index_code: str) -> tuple[date | None, date | None]:
        """查询指数日线数据的最早和最晚日期（读取走仓库）。"""
        return self._index_bar_repo.get_date_range(index_code)

    def get_index_data_quality(self, index_code: str) -> IndexDataQuality:
        """统计单指数的数据质量：日线覆盖范围、OHLC 缺失情况、估值覆盖与缺失。

        完全无数据时入队后台补数任务并返回零值统计（与 GET 读穿透语义一致）。
        OHLC 字段的"缺失"口径与 data_quality 模块一致：
        值为 None、NaN 或非正数均视为缺失/异常。

        Args:
            index_code: 指数代码。

        Returns:
            指数数据质量统计。
        """
        bars = self._index_bar_repo.find_all_by_code(index_code)
        valuations = self._valuation_repo.find_all_by_code(index_code)

        if not bars:
            self._enqueue_data_fill("index_bars", index_code)
        if not valuations:
            self._enqueue_data_fill("index_valuation", index_code)

        def _invalid(value: Any) -> bool:
            """判断价格字段是否缺失/异常（None、NaN 或非正值）。"""
            if value is None:
                return True
            try:
                if math.isnan(value):
                    return True
            except TypeError:
                pass
            return value <= 0

        bar_dates = [b.trade_date for b in bars]
        missing_open = sum(1 for b in bars if _invalid(b.open_price))
        missing_high = sum(1 for b in bars if _invalid(b.high_price))
        missing_low = sum(1 for b in bars if _invalid(b.low_price))
        missing_close = sum(1 for b in bars if _invalid(b.close_price))
        incomplete_rows = sum(
            1
            for b in bars
            if _invalid(b.open_price) or _invalid(b.high_price) or _invalid(b.low_price)
        )
        total_bars = len(bars)
        bar_quality = BarQuality(
            total=total_bars,
            min_date=min(bar_dates) if bar_dates else None,
            max_date=max(bar_dates) if bar_dates else None,
            missing_open=missing_open,
            missing_high=missing_high,
            missing_low=missing_low,
            missing_close=missing_close,
            incomplete_rows=incomplete_rows,
            incomplete_ratio=round(incomplete_rows / total_bars, 4) if total_bars else 0.0,
        )

        val_dates = [v.trade_date for v in valuations]
        valuation_quality = ValuationQuality(
            total=len(valuations),
            min_date=min(val_dates) if val_dates else None,
            max_date=max(val_dates) if val_dates else None,
            missing_pe=sum(1 for v in valuations if v.pe is None),
            missing_pb=sum(1 for v in valuations if v.pb is None),
            missing_dividend_yield=sum(1 for v in valuations if v.dividend_yield is None),
        )

        return IndexDataQuality(
            index_code=index_code,
            bars=bar_quality,
            valuations=valuation_quality,
        )

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

        针对每个基准指数，检查对应数据表中是否有记录、
        最新数据日期距今是否超过 3 个自然日（节假日容忍），返回汇总结果。
        """
        today = date.today()
        cal = TradingCalendar()
        # 使用最近交易日作为新鲜度基准，容忍 1 个交易日间隔
        latest_td = cal.latest_trading_day(today)
        stale_threshold = latest_td - timedelta(days=1)
        result: dict = {}

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

    # ==================================================================
    # 共享私有方法
    # ==================================================================

    # ==================================================================
    # 全量日频摄取（后台线程入口）
    # ==================================================================

    def _try_acquire_ingest_lock(self, run_id: str) -> bool:
        """尝试获取摄取互斥锁，失败时将运行记录标记为 skipped。

        daily_ingest 与两个手动刷新入口（指数/宏观）共享同一把进程内互斥锁，
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
        1. 所有基准指数的日线和估值
        2. 宏观指标（CPI/PMI/LPR）

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

            # ------------------------------ 1. 指数日线 + 估值 ------------------------------
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

            # ------------------------------ 2. 宏观指标 ------------------------------
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

    # ==================================================================
    # 单指数数据维护（后台线程入口）
    # ==================================================================

    def rebuild_index_data(self, run_id: str, index_code: str) -> None:
        """单指数全量覆盖重拉：删除该指数历史日线与估值后重新拉取全量数据。

        先拉取外部数据、后删除旧数据，任一环节失败都会回滚，保证不会出现
        "旧数据已删、新数据未入库"的中间态。

        Args:
            run_id: 运行记录 ID。
            index_code: 指数代码。
        """
        if self._index_repo.find_by_code(index_code) is None:
            self._run_svc.mark_failed(run_id, f"指数不存在: {index_code}")
            return
        if not self._try_acquire_ingest_lock(run_id):
            return
        start_time = utcnow()
        try:
            self._run_svc.mark_running(run_id)

            # 1. 先拉取全量数据（失败时不触碰现有数据）
            bars = AkShareIndexClient().fetch_index_daily(index_code)
            valuations = AkShareIndexClient().fetch_index_valuation(index_code)

            # 2. 删除旧数据
            deleted_bars = (
                self._db.query(IndexDailyBarModel)
                .filter(IndexDailyBarModel.index_code == index_code)
                .delete(synchronize_session=False)
            )
            deleted_valuations = (
                self._db.query(IndexValuationModel)
                .filter(IndexValuationModel.index_code == index_code)
                .delete(synchronize_session=False)
            )

            # 3. 写入新数据并统一提交（失败回滚后旧数据保留）
            bar_records = self._insert_index_bars(index_code, bars)
            valuation_records = self._insert_index_valuations(index_code, valuations)
            self._db.commit()

            self._run_svc.mark_success(
                run_id,
                metrics={
                    "index_code": index_code,
                    "deleted_bar_records": deleted_bars,
                    "deleted_valuation_records": deleted_valuations,
                    "bar_records": bar_records,
                    "valuation_records": valuation_records,
                    "duration_seconds": round((utcnow() - start_time).total_seconds(), 1),
                },
            )
        except Exception as e:
            self._db.rollback()
            logger.warning("指数 %s 全量覆盖重拉失败: %s", index_code, e, exc_info=True)
            self._run_svc.mark_failed(run_id, str(e)[:1000])
        finally:
            _daily_ingest_lock.release()

    def incremental_fill_index_data(self, run_id: str, index_code: str) -> None:
        """单指数增量补数据：从数据库最新交易日补充到当天（后台线程入口）。

        与 refresh_index_data 一致：非交易日标记 skipped，避免重复执行。

        Args:
            run_id: 运行记录 ID。
            index_code: 指数代码。
        """
        if self._index_repo.find_by_code(index_code) is None:
            self._run_svc.mark_failed(run_id, f"指数不存在: {index_code}")
            return
        if not self._try_acquire_ingest_lock(run_id):
            return
        start_time = utcnow()
        try:
            self._run_svc.mark_running(run_id)

            today = date.today()
            if not TradingCalendar().is_trading_day(today):
                self._run_svc.mark_skipped(
                    run_id,
                    metrics={"reason": "holiday", "message": "非交易日，跳过数据摄取"},
                )
                return

            bar_records = self._fetch_and_upsert_index_bars(index_code)
            valuation_records = self._fetch_and_upsert_index_valuation(index_code)

            self._run_svc.mark_success(
                run_id,
                metrics={
                    "index_code": index_code,
                    "bar_records": bar_records,
                    "valuation_records": valuation_records,
                    "duration_seconds": round((utcnow() - start_time).total_seconds(), 1),
                },
            )
        except Exception as e:
            self._db.rollback()
            logger.warning("指数 %s 增量补数据失败: %s", index_code, e, exc_info=True)
            self._run_svc.mark_failed(run_id, str(e)[:1000])
        finally:
            _daily_ingest_lock.release()

    def run_cold_start(self, run_id: str) -> None:
        """冷启动：拉取全部指数的全量历史数据（后台线程入口）。

        与 run_daily_ingest 的区别：
        - 指数日线拉取全量历史
        - 跳过周末检查（冷启动可随时执行）
        - 指数日线/估值/宏观复用现有全量方法
        """
        start_time = utcnow()
        try:
            self._run_svc.mark_running(run_id)

            # 1. 指数全量日线 + 估值
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

            # 2. 宏观指标
            macro_count = 0
            try:
                macro_count = self._fetch_and_upsert_macro()
            except Exception as e:
                logger.warning("宏观指标拉取失败: %s", e)

            # 汇总
            self._run_svc.mark_success(
                run_id,
                metrics={
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
        """启动补全：检查所有指数的数据缺口并按需补全（系统启动时自动调用）。

        与 run_cold_start 的区别：
        - 指数/宏观数据仅在超过 5 天未更新时才重新拉取
        """
        start_time = utcnow()
        stale_threshold = date.today() - timedelta(days=5)

        try:
            self._run_svc.mark_running(run_id)

            # 1. 指数日线 + 估值（超过 5 天未更新才重新拉取）
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

            # 2. 宏观指标（超过 5 天未入库才重新拉取）
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
