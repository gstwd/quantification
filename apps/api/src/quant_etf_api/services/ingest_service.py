from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.clients.akshare_macro import AkShareMacroClient
from quant_etf_api.infra.clients.baostock_index import BaostockIndexClient
from quant_etf_api.infra.clients.index_daily_common import (
    IndexDailyBar,
    incremental_start_date,
    compare_index_bar_overlap,
    ohlc_missing_count,
)
from quant_etf_api.infra.clients.tickflow_index import TickFlowIndexClient
from quant_etf_api.infra.clients.tushare_index import TushareIndexClient
from quant_etf_api.infra.clients.tushare_market import (
    TushareIndexValuationClient,
    TushareMacroClient,
)
from quant_etf_api.domain.common.trading_calendar import TradingCalendarUnavailableError
from quant_etf_api.infra.trading_calendar import resolve_trading_calendar
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    IndexDailyBarModel,
    IndexValuationModel,
    MacroIndicatorModel,
)
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.db.repositories.macro_indicator import MacroIndicatorRepository
from quant_etf_api.schemas.market_data import (
    DailyBar,
    IndexSummary,
    IndexValuation,
    MacroIndicatorSchema,
)

logger = logging.getLogger(__name__)


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
    """指数/宏观数据抓取与读取服务。

    只做三件事：多数据源抓取与字段归一化、幂等写入、读穿透缓存；读取统一走仓库。
    批量同步、缺口修复、全量重拉、质量检查与运行记录生命周期统一由
    ``DataManagementService`` 编排，本服务只作为其抓取后端，
    因此不再持有运行状态流转逻辑与进程内互斥锁。
    """

    def __init__(self, db: Session) -> None:
        """初始化数据摄取服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._index_bar_repo = IndexDailyBarRepository(db)
        self._valuation_repo = IndexValuationRepository(db)
        self._macro_repo = MacroIndicatorRepository(db)
        self._last_fallbacks: list[dict[str, str]] = []

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

    # ==================================================================
    # 指数日线（多数据源）
    # ==================================================================

    def _insert_index_bars(
        self,
        index_code: str,
        bars: list[Any],
        source: str = "akshare",
        *,
        overwrite: bool = False,
    ) -> int:
        """将日线数据批量幂等写入 index_daily_bar（不提交，由调用方统一提交）。

        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制。

        Args:
            index_code: 指数代码。
            bars: 待写入的日线数据列表（多数据源客户端统一返回 IndexDailyBar）。
            source: 实际提供数据的数据源标识（如 akshare/tickflow/tushare/baostock）。

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
                "source": source,
                "ingested_at": utcnow(),
            }
            for b in bars
        ]
        for i in range(0, len(values), batch_size):
            batch = values[i : i + batch_size]
            stmt = insert(IndexDailyBarModel).values(batch)
            if overwrite:
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_index_daily_bar",
                    set_={
                        "open_price": stmt.excluded.open_price,
                        "high_price": stmt.excluded.high_price,
                        "low_price": stmt.excluded.low_price,
                        "close_price": stmt.excluded.close_price,
                        "prev_close_price": stmt.excluded.prev_close_price,
                        "change_pct": stmt.excluded.change_pct,
                        "volume": stmt.excluded.volume,
                        "turnover": stmt.excluded.turnover,
                        "source": stmt.excluded.source,
                        "ingested_at": stmt.excluded.ingested_at,
                    },
                )
            else:
                stmt = stmt.on_conflict_do_nothing(constraint="uq_index_daily_bar")
            self._db.execute(stmt)
        return len(bars)

    def _build_index_daily_sources(self) -> list[tuple[str, Any]]:
        """按配置优先级构建指数日线数据源列表。

        优先级来自 settings.index_daily_source_order（逗号分隔），
        非法名称跳过并告警；tickflow 默认免费档可用（无需 Key），
        tushare 未配置 Token 时自动跳过。

        Returns:
            [(数据源标识, 客户端实例), ...] 列表，按优先级排序。
        """
        registry: dict[str, Any] = {
            "akshare": AkShareIndexClient,
            "tickflow": TickFlowIndexClient,
            "tushare": TushareIndexClient,
            "baostock": BaostockIndexClient,
        }
        order = get_settings().index_daily_source_order
        sources: list[tuple[str, Any]] = []
        for name in (part.strip() for part in order.split(",")):
            if not name:
                continue
            client_cls = registry.get(name)
            if client_cls is None:
                logger.warning("未知的指数日线数据源 %s，已忽略", name)
                continue
            client = client_cls()
            # tushare 依赖 Token，未配置时不加入候选，避免每次调用都打警告
            if isinstance(client, TushareIndexClient) and not client.is_configured():
                logger.info("未配置 TUSHARE_TOKEN，跳过 tushare 指数日线源")
                continue
            sources.append((name, client))
        return sources

    def _fetch_index_daily_multi_source(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[list[IndexDailyBar], str]:
        """多数据源拉取指数日线：OHLC 任一缺失即不合格，最终采用缺失最少的数据源。

        切换规则（与 AkShareIndexClient.fetch_index_daily 内部降级链口径一致）：
        - 按配置优先级依次调用各 SDK 客户端，任一源抛错或返回空数据时继续降级；
        - 请求窗口内开盘/最高/最低/收盘任一缺失（NaN/非正值）即视为该源不合格，
          记录其缺失交易日数量后继续拉取下一数据源；
        - 首个 OHLC 完全完整的数据源直接采用（缺失 0 为理论最小值，无需再试后续源）；
        - 所有源均不合格时回退缺失交易日最少的数据源并告警（尽量保证有数据可用）；
        - 命中候选源时与前一候选对比共同交易日收盘点位，记录跨源一致性日志。

        Args:
            index_code: 指数代码，如 000300。
            start_date: 起始日 'YYYYMMDD'，None 表示最早。
            end_date: 结束日 'YYYYMMDD'，None 表示最新。

        Returns:
            (日线列表, 数据源标识)；全部失败时返回 ([], "")。
        """
        sources = self._build_index_daily_sources()
        attempts: list[tuple[str, str]] = []
        if not sources:
            logger.warning("指数 %s 无可用日线数据源", index_code)
            self._last_fallbacks = []
            return [], ""

        last_error: Exception | None = None
        best_source = ""
        best_bars: list[IndexDailyBar] = []
        best_missing = -1
        for source, client in sources:
            try:
                bars = client.fetch_index_daily(index_code, start_date, end_date)
            except Exception as e:
                last_error = e
                attempts.append((source, type(e).__name__))
                logger.warning(
                    "指数 %s 日线源 %s 拉取失败，降级到下一源: %s",
                    index_code,
                    source,
                    e,
                )
                continue
            if not bars:
                attempts.append((source, "empty_response"))
                logger.info("指数 %s 日线源 %s 返回空数据，降级到下一源", index_code, source)
                continue
            missing = ohlc_missing_count(bars)
            if missing:
                attempts.append((source, "invalid_ohlc"))
            if missing == 0:
                # 与前一非空候选（若有）对比收盘点位，核对跨源数据一致性
                if best_bars:
                    common, max_diff = compare_index_bar_overlap(best_bars, bars)
                    if max_diff is not None:
                        logger.info(
                            "指数 %s 数据源 %s 与 %s 共同交易日 %d 天，收盘点位最大差 %.4f",
                            index_code,
                            best_source,
                            source,
                            common,
                            max_diff,
                        )
                logger.info("指数 %s 日线最终由 %s 提供，共 %d 条", index_code, source, len(bars))
                self._record_fallbacks(index_code, source, attempts)
                return bars, source
            if best_bars and missing < best_missing:
                common, max_diff = compare_index_bar_overlap(best_bars, bars)
                if max_diff is not None:
                    logger.info(
                        "指数 %s 数据源 %s 与 %s 共同交易日 %d 天，收盘点位最大差 %.4f",
                        index_code,
                        best_source,
                        source,
                        common,
                        max_diff,
                    )
            if not best_bars or missing < best_missing:
                best_source = source
                best_bars = bars
                best_missing = missing
            logger.warning(
                "指数 %s 日线源 %s OHLC 缺失 %d 日，视为不合格，继续降级",
                index_code,
                source,
                missing,
            )

        if best_bars:
            logger.warning(
                "指数 %s 所有日线源 OHLC 均不完整，回退到缺失最少的数据源 %s（缺失 %d 日，共 %d 条）",
                index_code,
                best_source,
                best_missing,
                len(best_bars),
            )
            self._record_fallbacks(index_code, best_source, attempts)
            return best_bars, best_source
        if last_error is not None:
            self._last_fallbacks = []
            raise last_error
        self._last_fallbacks = []
        return [], ""

    def _record_fallbacks(
        self, partition_key: str, selected_source: str, attempts: list[tuple[str, str]]
    ) -> None:
        """记录本次请求的来源降级链，不保存上游原始响应。"""
        if attempts and attempts[0][0] != selected_source:
            self._last_fallbacks = [
                {
                    "partition_key": partition_key,
                    "from": attempts[0][0],
                    "to": selected_source,
                    "reason": attempts[0][1],
                }
            ]
        else:
            self._last_fallbacks = []

    def _fetch_and_upsert_index_bars(
        self,
        index_code: str,
        incremental: bool = True,
        *,
        overwrite: bool = False,
    ) -> int:
        """从多数据源拉取指数日线并幂等写入 index_daily_bar。

        增量模式（默认）：从 DB 最新日期回退缓冲窗口拉取，仅写入最新日期之后的数据；
        全量模式（冷启动）：拉取全量历史数据。数据源切换由
        _fetch_index_daily_multi_source 统一管理，入库时记录实际数据源。

        Returns:
            写入记录数
        """
        latest = self._index_bar_repo.get_latest_date(index_code) if incremental else None
        if latest is not None:
            # 增量拉取：客户端从 latest 回退缓冲窗口，保证边界 bar 的涨跌幅可算
            start = incremental_start_date(latest)
            bars, source = self._fetch_index_daily_multi_source(
                index_code, start_date=start.strftime("%Y%m%d")
            )
        else:
            bars, source = self._fetch_index_daily_multi_source(index_code)
        if not bars:
            return 0

        # 增量模式：仅保留 DB 中不存在的记录（同时丢弃缓冲窗口内的重复行）
        if latest is not None:
            bars = [b for b in bars if b.trade_date > latest]

        # 非交易日行情过滤（F-7）：上游偶尔把上一交易日的收盘价打在假期日期上
        # （实测 2018-06-18 端午节有 7 个指数日线），入库前按交易日历剔除
        bars, dropped = self._drop_non_trading_bars(bars)
        if dropped:
            logger.warning(
                "剔除非交易日行情: index_code=%s 条数=%d 日期=%s",
                index_code,
                len(dropped),
                [str(b.trade_date) for b in dropped[:5]],
            )
        if not bars:
            return 0

        if overwrite:
            count = self._insert_index_bars(
                index_code, bars, source=source, overwrite=True
            )
        else:
            count = self._insert_index_bars(index_code, bars, source=source)
        self._db.commit()
        return count

    def _drop_non_trading_bars(self, bars: list[Any]) -> tuple[list[Any], list[Any]]:
        """剔除不属于交易日的行情（F-7）。

        `index_daily_bar` 没有对交易日历的约束，上游把假期日期打上行情时
        会被照单全收，随后回测把它当交易日，多出一个假调仓日与假收益。
        日历不可用时保持原有行为（不阻断摄取）并留下告警。

        Args:
            bars: 待入库的行情列表（元素含 ``trade_date``）。

        Returns:
            (可入库行情列表, 被剔除行情列表)。
        """
        if not bars:
            return bars, []
        try:
            calendar, _ = resolve_trading_calendar(
                self._db,
                required_range=(
                    min(b.trade_date for b in bars),
                    max(b.trade_date for b in bars),
                ),
            )
        except TradingCalendarUnavailableError:
            logger.warning("交易日历不可用，跳过行情的非交易日校验", exc_info=True)
            return bars, []
        kept = [b for b in bars if calendar.is_trading_day(b.trade_date)]
        dropped = [b for b in bars if not calendar.is_trading_day(b.trade_date)]
        return kept, dropped

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
    # 指数估值 PE/PB（Tushare 优先，AkShare 兜底）
    # ==================================================================

    def _insert_index_valuations(
        self,
        index_code: str,
        valuations: list[Any],
        *,
        overwrite: bool = False,
    ) -> int:
        """将估值数据批量幂等写入 index_valuation（不提交，由调用方统一提交）。

        分批写入，避免单条 INSERT 参数超过 PostgreSQL 65535 限制
        （每行 9 字段，批次上限 7000 行 = 63000 参数）。

        Args:
            index_code: 指数代码。
            valuations: 待写入的估值数据列表（Tushare/AkShare 客户端返回）。

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
            stmt = insert(IndexValuationModel).values(batch)
            if overwrite:
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_index_valuation",
                    set_={
                        "pe": stmt.excluded.pe,
                        "pe_percentile": stmt.excluded.pe_percentile,
                        "pb": stmt.excluded.pb,
                        "pb_percentile": stmt.excluded.pb_percentile,
                        "dividend_yield": stmt.excluded.dividend_yield,
                        "source": stmt.excluded.source,
                        "ingested_at": stmt.excluded.ingested_at,
                    },
                )
            else:
                stmt = stmt.on_conflict_do_nothing(constraint="uq_index_valuation")
            self._db.execute(stmt)
        return len(valuations)

    def _fetch_index_valuation_preferred(self, index_code: str) -> list[Any]:
        """按“Tushare 优先、AkShare 兜底”拉取指数估值。

        Tushare index_dailybasic 仅覆盖 000300/000016/000905 等少数指数；
        覆盖范围外的指数直接走 AkShare（乐咕乐股 → 中证官网降级链）。Tushare
        拉取失败或返回空时同样回退 AkShare，保证多源容错。

        Args:
            index_code: 指数代码，如 000300。

        Returns:
            按日期升序排列的估值列表；两个来源都无数据时返回空列表。
        """
        tushare_client = TushareIndexValuationClient()
        attempts: list[tuple[str, str]] = []
        if tushare_client.is_configured() and tushare_client.supports(index_code):
            try:
                values = tushare_client.fetch_index_valuation(index_code)
                if values:
                    self._record_fallbacks(index_code, "tushare", attempts)
                    logger.info(
                        "指数 %s 估值由 tushare 提供，共 %d 条",
                        index_code,
                        len(values),
                    )
                    return values
                attempts.append(("tushare", "empty_response"))
                logger.info("指数 %s tushare 估值返回空，回退 AkShare", index_code)
            except Exception as exc:
                attempts.append(("tushare", type(exc).__name__))
                logger.warning(
                    "指数 %s tushare 估值拉取失败，回退 AkShare: %s",
                    index_code,
                    exc,
                )
        values = AkShareIndexClient().fetch_index_valuation(index_code)
        self._record_fallbacks(index_code, "akshare", attempts)
        return values

    def _fetch_and_upsert_index_valuation(
        self,
        index_code: str,
        *,
        overwrite: bool = False,
    ) -> int:
        """拉取指数 PE/PB 估值（Tushare 优先）并幂等写入 index_valuation。

        Returns:
            写入记录数
        """
        valuations = self._fetch_index_valuation_preferred(index_code)
        if not valuations:
            return 0
        if overwrite:
            count = self._insert_index_valuations(index_code, valuations, overwrite=True)
        else:
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

    # ==================================================================
    # 宏观指标（Tushare 优先，AkShare 兜底）
    # ==================================================================

    def _fetch_macro_indicators_preferred(self) -> list[Any]:
        """按数据组拉取宏观指标：每组先试 Tushare，失败或空时回退 AkShare。

        CPI/PMI/LPR 三组独立降级：LPR 接口（shibor_lpr）在 2000 积分档位
        约 1 次/小时，被限频时只影响 LPR 组，不影响 CPI/PMI。

        Returns:
            MacroIndicator 列表（source 标识实际来源）。
        """
        tushare_client = TushareMacroClient()
        akshare_client = AkShareMacroClient()
        groups: list[tuple[str, Any, Any]] = [
            ("CPI", tushare_client.fetch_cpi_monthly, akshare_client.fetch_cpi_monthly),
            ("PMI", tushare_client.fetch_pmi, akshare_client.fetch_pmi),
            ("LPR", tushare_client.fetch_lpr, akshare_client.fetch_lpr),
        ]
        results: list[Any] = []
        fallbacks: list[dict[str, str]] = []
        for name, tushare_fetch, akshare_fetch in groups:
            rows: list[Any] = []
            tushare_attempted = False
            tushare_reason = ""
            if tushare_client.is_configured():
                tushare_attempted = True
                try:
                    rows = list(tushare_fetch() or [])
                except Exception as exc:
                    tushare_reason = type(exc).__name__
                    logger.warning(
                        "tushare %s 拉取失败，回退 AkShare: %s",
                        name,
                        exc,
                    )
            if not rows:
                try:
                    rows = list(akshare_fetch() or [])
                except Exception as exc:
                    logger.warning("AkShare %s 拉取失败: %s", name, exc)
                if tushare_attempted and rows:
                    fallbacks.append(
                        {
                            "partition_key": name.lower(),
                            "from": "tushare",
                            "to": "akshare",
                            "reason": tushare_reason or "empty_response",
                        }
                    )
            results.extend(rows)
        self._last_fallbacks = fallbacks
        return results

    def _fetch_and_upsert_macro(self, *, overwrite: bool = False) -> int:
        """拉取所有宏观指标（Tushare 优先）并幂等写入 macro_indicator。

        Returns:
            写入记录数
        """
        indicators = self._fetch_macro_indicators_preferred()
        if not indicators:
            return 0
        stmt = insert(MacroIndicatorModel).values(
            [
                {
                    "indicator_code": i.indicator_code,
                    "indicator_name": i.indicator_name,
                    "period": i.period,
                    "value": i.value,
                    "unit": i.unit,
                    "source": getattr(i, "source", None) or "akshare",
                    "period_date": i.period_date,
                    "ingested_at": utcnow(),
                }
                for i in indicators
            ]
        )
        if overwrite:
            stmt = stmt.on_conflict_do_update(
                constraint="uq_macro_indicator",
                set_={
                    "indicator_name": stmt.excluded.indicator_name,
                    "value": stmt.excluded.value,
                    "unit": stmt.excluded.unit,
                    "source": stmt.excluded.source,
                    "period_date": stmt.excluded.period_date,
                    "ingested_at": stmt.excluded.ingested_at,
                },
            )
        else:
            stmt = stmt.on_conflict_do_nothing(constraint="uq_macro_indicator")
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

