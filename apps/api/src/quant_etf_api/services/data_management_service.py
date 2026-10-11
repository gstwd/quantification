"""统一数据管理服务。

本模块以静态数据集清单把不同外部数据的质量快照、维护操作和运行明细
收敛到同一入口；数据抓取仍由既有领域服务负责，避免重复实现客户端逻辑。
"""

from __future__ import annotations

import logging
import math
import threading
from bisect import bisect_left
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Date, and_, case, cast, func, literal, not_, or_, select
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.clients.tushare_market import TushareIndexValuationClient
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    DataHealthSnapshotModel,
    IndexDailyBarModel,
    IndexMemberEventModel,
    IndexValuationModel,
    MacroIndicatorModel,
    TradingCalendarModel,
)
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
from quant_etf_api.infra.time import today_cn
from quant_etf_api.infra.trading_calendar import TradingCalendar, validate_calendar_completeness
from quant_etf_api.domain.common.trading_calendar import TradingCalendarUnavailableError
from quant_etf_api.schemas.data_management import (
    DataManagementOverview,
    DataPartitionHealth,
    DataSetDetailResponse,
    DataSetHealthSummary,
)
from quant_etf_api.services.run_service import RunService

logger = logging.getLogger(__name__)

# 数据管理操作可能跨多个既有服务，必须全局串行化，避免并发写同一批表。
# API 进程内 worker 与独立 worker 进程可能同时存在，因此 PostgreSQL 下使用
# 跨进程的会话级 advisory lock；其他方言（单元测试）回退为进程内线程锁。
# 不同作用域的任务入队后各自到达 worker，用阻塞锁排队而不是直接失败，
# 避免"全局同步进行中用户点单数据集检查"被当作运行失败。
_operation_lock = threading.Lock()
# advisory lock 键：固定的 64 位整数，标识"数据管理写操作"这一把全局锁
_ADVISORY_LOCK_KEY = 7_320_114_201_660_211
# 低频数据集（成分/目录）在同步时按此天数判断是否需要再次拉取上游
_LOW_FREQ_REFRESH_DAYS = 7
# 个股 Tushare 回填统一起点，与 StockDataService._STOCK_FETCH_EPOCH 保持一致
_STOCK_TUSHARE_EPOCH = date(2013, 1, 1)
_STOCK_DAILY_DATASETS = {
    "stock_daily_close",
    "stock_daily_basic",
    "stock_moneyflow",
}
_UPSTREAM_EXEMPT_DAILY_DATASETS = _STOCK_DAILY_DATASETS | {
    "index_daily_bar",
    "index_valuation",
    "industry_daily_bar",
}
_STOCK_REPAIR_COMMIT_BATCH_SIZE = 20
_DAILY_REPAIR_COMMIT_BATCH_SIZE = 8
_INDEX_REPAIR_WINDOW_GAP_DAYS = 10
# 指数/行业日线涨跌幅分级：超过告警线需人工确认，超过错误线视为脏数据
_CHANGE_PCT_WARNING_LIMIT = 15
_CHANGE_PCT_ERROR_LIMIT = 30
# "上游仅提供收盘价"确认区间的来源标识，与上游缺失区间共用存储但语义不同
_CLOSE_ONLY_SOURCE = "close_only"
# 日历安全重建时允许删除的本地多余日期上限，超过视为上游异常而中止
_CALENDAR_REBUILD_MAX_REMOVALS = 5
_VALUATION_SUPPORTED_CODES = {
    "000016",
    "000300",
    "000905",
}
# 健康快照全部合法状态，供接口过滤参数校验
HEALTH_STATUSES = ("healthy", "warning", "error", "unknown", "unsupported")
# "有问题"的口径：异常与需关注，用于分区级快速筛选
_PROBLEM_HEALTH_STATUSES = ("warning", "error")


def _escape_like_pattern(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当作模式匹配。

    Args:
        value: 原始关键字。

    Returns:
        转义后的关键字（反斜杠、百分号、下划线均被转义）。
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _dataset_status(metrics: dict[str, Any]) -> str:
    """由单数据集执行指标推导数据集级状态。

    Args:
        metrics: _execute_dataset 返回的指标。

    Returns:
        success：无分区错误；partial_success：部分分区成功；failed：全部分区失败。
    """
    errors = metrics.get("errors") or []
    if not errors:
        return "success"
    return "failed" if not metrics.get("records") else "partial_success"


@dataclass(frozen=True)
class DataSetDefinition:
    """静态数据集清单中的单项定义。"""

    key: str
    name: str
    frequency: str
    source_label: str
    partition_label: str | None
    operations: tuple[str, ...]
    quality_rules: tuple[str, ...]


DATASETS: tuple[DataSetDefinition, ...] = (
    DataSetDefinition(
        "trading_calendar",
        "A 股交易日历",
        "日历",
        "Tushare / AkShare 兜底",
        None,
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("最近交易日可用性",),
    ),
    DataSetDefinition(
        "index_daily_bar",
        "指数日线行情",
        "日频",
        "Tushare 优先 · 多源兜底",
        "指数",
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("交易日连续性", "OHLC 与涨跌幅合法性", "非交易日行", "最近交易日覆盖"),
    ),
    DataSetDefinition(
        "index_valuation",
        "指数估值",
        "日频",
        "Tushare / AkShare 兜底",
        "指数",
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("估值覆盖率", "PE/PB 与百分位合法性", "最近可用日期"),
    ),
    DataSetDefinition(
        "macro_indicator",
        "宏观指标",
        "月频/事件",
        "Tushare / AkShare 兜底",
        "指标",
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("发布周期新鲜度", "数值字段完整性"),
    ),
    DataSetDefinition(
        "index_membership",
        "指数成分",
        "月频/快照",
        "Tushare / AkShare / Baostock",
        "指数",
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("当前快照新鲜度", "成员代码与生效日期完整性"),
    ),
    DataSetDefinition(
        "industry_universe",
        "申万行业基础信息",
        "低频",
        "申万官网",
        None,
        ("sync_latest", "check"),
        ("目录数量", "行业代码与名称完整性"),
    ),
    DataSetDefinition(
        "industry_daily_bar",
        "申万行业日线",
        "日频",
        "申万官网 / AkShare",
        "行业",
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("交易日连续性", "OHLC 与涨跌幅合法性", "非交易日行", "最近交易日覆盖"),
    ),
    DataSetDefinition(
        "industry_membership",
        "申万行业成分",
        "低频",
        "申万官网",
        "行业",
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("刷新期限", "股票、行业与生效日期完整性"),
    ),
    DataSetDefinition(
        "stock_universe",
        "证券基础信息",
        "低频",
        "Tushare",
        None,
        ("sync_latest", "check"),
        ("代码与名称完整性",),
    ),
    DataSetDefinition(
        "stock_daily_close",
        "个股日线行情",
        "日频",
        "Tushare",
        "股票",
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("交易日连续性", "OHLC 合法性", "最近交易日覆盖"),
    ),
    DataSetDefinition(
        "stock_daily_basic",
        "个股每日指标",
        "日频",
        "Tushare",
        "股票",
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("交易日连续性", "估值与股本字段完整性", "最近交易日覆盖"),
    ),
    DataSetDefinition(
        "stock_moneyflow",
        "个股资金流向",
        "日频",
        "Tushare",
        "股票",
        ("sync_latest", "check", "repair_gaps", "rebuild"),
        ("交易日连续性", "资金流向字段完整性", "最近交易日覆盖"),
    ),
)
_DATASET_BY_KEY = {item.key: item for item in DATASETS}


class DataManagementService:
    """编排所有非新闻外部数据集的健康检查与维护操作。"""

    def __init__(self, db: Session) -> None:
        """初始化统一数据管理服务。

        Args:
            db: SQLAlchemy 同步会话。
        """
        self._db = db

    def overview(self) -> DataManagementOverview:
        """返回所有数据集的当前健康摘要。

        快照不会在服务启动时自动生成：只有手动触发维护操作或数据摄取任务
        执行后才会写入。因此这里额外回报快照行数，供前端在完全没有快照时
        给出"尚未检查"的友好提示，而不是把 unknown 当成异常。

        Returns:
            数据管理页所需的摘要与状态计数。
        """
        from quant_etf_api.config.settings import get_settings

        settings = get_settings()
        items = [self._summary(definition) for definition in DATASETS]
        snapshot_count = int(
            self._db.query(func.count(DataHealthSnapshotModel.partition_key)).scalar() or 0
        )
        return DataManagementOverview(
            schedule_time=f"{settings.schedule_time} 自动全局同步（北京时间）",
            datasets=items,
            healthy_count=sum(item.health_status == "healthy" for item in items),
            warning_count=sum(item.health_status == "warning" for item in items),
            error_count=sum(item.health_status == "error" for item in items),
            unknown_count=sum(item.health_status == "unknown" for item in items),
            unsupported_count=sum(item.health_status == "unsupported" for item in items),
            snapshot_count=snapshot_count,
        )

    def detail(
        self,
        dataset_key: str,
        offset: int,
        limit: int,
        partition_key: str | None = None,
        *,
        health_status: str | None = None,
        problem_only: bool = False,
        keyword: str | None = None,
    ) -> DataSetDetailResponse:
        """返回指定数据集的规则与分区健康快照。

        分区数据集（个股/行业/指数）可能有上千个分区，逐页翻找问题分区不现实，
        因此支持服务端筛选：`problem_only` 只看异常与需关注分区，`health_status`
        按单一状态精确过滤，`keyword` 按分区代码或名称模糊匹配。三者可叠加，
        `total` 始终反映过滤后的分区数。

        Args:
            dataset_key: 静态数据集键。
            offset: 分区分页偏移量。
            limit: 分区分页大小。
            partition_key: 可选分区键，指定时仅返回该分区（精确匹配，供深链使用）。
            health_status: 可选健康状态精确过滤（healthy/warning/error/unknown/unsupported）。
            problem_only: 是否只返回异常（error）与需关注（warning）分区。
            keyword: 可选模糊关键字，同时匹配分区代码与分区名称。

        Returns:
            数据集详情。

        Raises:
            ValueError: 数据集不存在时抛出。
        """
        definition = self._definition(dataset_key)
        query = self._db.query(DataHealthSnapshotModel).filter(
            DataHealthSnapshotModel.dataset_key == dataset_key,
            DataHealthSnapshotModel.partition_key != "",
        )
        if partition_key:
            query = query.filter(DataHealthSnapshotModel.partition_key == partition_key)
        if health_status:
            query = query.filter(DataHealthSnapshotModel.health_status == health_status)
        elif problem_only:
            query = query.filter(
                DataHealthSnapshotModel.health_status.in_(_PROBLEM_HEALTH_STATUSES)
            )
        if keyword:
            pattern = f"%{_escape_like_pattern(keyword)}%"
            query = query.filter(
                or_(
                    DataHealthSnapshotModel.partition_key.ilike(pattern, escape="\\"),
                    DataHealthSnapshotModel.partition_name.ilike(pattern, escape="\\"),
                )
            )
        total = query.count()
        rows = (
            query.order_by(DataHealthSnapshotModel.partition_key).offset(offset).limit(limit).all()
        )
        return DataSetDetailResponse(
            dataset=self._summary(definition),
            quality_rules=list(definition.quality_rules),
            items=[self._partition_schema(definition, row) for row in rows],
            total=total,
            offset=offset,
            limit=limit,
        )

    def execute(
        self,
        operation: str,
        dataset_key: str | None,
        partition_key: str | None,
        run_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """同步执行一个已入队的数据管理操作。

        Args:
            operation: sync_latest/check/repair_gaps/rebuild。
            dataset_key: 数据集键，None 表示全局操作。
            partition_key: 可选单对象分区键。
            run_id: 父运行记录 ID。
            force: 是否绕过"已是最新/未到刷新周期"的节流直接拉取上游。

        Returns:
            可写入运行指标的汇总结果。

        Raises:
            ValueError: 操作范围或数据集键非法时抛出。
        """
        with self._exclusive_operation():
            definitions = [self._definition(dataset_key)] if dataset_key else list(DATASETS)
            result_items: list[dict[str, Any]] = []
            run_service = RunService(self._db)
            for definition in definitions:
                try:
                    metrics = self._execute_dataset(
                        definition, operation, partition_key, run_id, force
                    )
                    status = _dataset_status(metrics)
                    result_items.append(
                        {"dataset_key": definition.key, "status": status, **metrics}
                    )
                    run_service.add_item(run_id, definition.key, status, metrics=metrics)
                except Exception as exc:
                    self._db.rollback()
                    message = f"{type(exc).__name__}: {exc}"
                    # 质量检查依赖本地交易日历快照；日历不可用时不能把未知状态
                    # 覆盖成错误快照，否则会丢失上一次有效检查结果。
                    if not (
                        operation == "check"
                        and isinstance(exc, TradingCalendarUnavailableError)
                    ):
                        self._mark_failure(definition, partition_key, run_id, message)
                    result_items.append(
                        {"dataset_key": definition.key, "status": "failed", "error": message}
                    )
                    run_service.add_item(run_id, definition.key, "failed", message)
            statuses = [item["status"] for item in result_items]
            success_count = sum(status == "success" for status in statuses)
            failed_count = sum(status == "failed" for status in statuses)
            partial_count = len(result_items) - success_count - failed_count
            return {
                "items": result_items,
                "success_count": success_count,
                "failed_count": failed_count,
                "partial_count": partial_count,
                "status": (
                    "success"
                    if failed_count == 0 and partial_count == 0
                    else "partial_success"
                    if success_count > 0 or partial_count > 0
                    else "failed"
                ),
            }

    @contextmanager
    def _exclusive_operation(self) -> Iterator[None]:
        """获取数据管理写操作的全局互斥锁，退出时释放。

        PostgreSQL 下在一条独立连接上持有会话级 ``pg_advisory_lock``：
        ORM 会话在多次 commit 之间会归还连接，不能把锁挂在会话连接上。
        其他方言回退为进程内线程锁。
        """
        bind = self._db.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
            with _operation_lock:
                yield
            return
        engine = getattr(bind, "engine", bind)
        with engine.connect() as lock_conn:
            lock_conn.execute(select(func.pg_advisory_lock(_ADVISORY_LOCK_KEY)))
            lock_conn.commit()
            try:
                yield
            finally:
                lock_conn.execute(select(func.pg_advisory_unlock(_ADVISORY_LOCK_KEY)))
                lock_conn.commit()

    def _execute_dataset(
        self,
        definition: DataSetDefinition,
        operation: str,
        partition_key: str | None,
        run_id: str,
        force: bool,
    ) -> dict[str, Any]:
        """执行单数据集操作并刷新当前快照。

        Args:
            definition: 数据集定义。
            operation: 维护操作。
            partition_key: 可选分区键。
            run_id: 父运行 ID。
            force: 是否强制拉取上游。

        Returns:
            {checked_partitions, operation, records, error_count, errors, skipped}。
            分区级抓取错误已收集在 errors 中，不再以异常中断整个数据集。
        """
        if operation not in definition.operations:
            raise ValueError(f"数据集 {definition.key} 不支持操作 {operation}")
        sync_result: dict[str, Any] = {
            "records": 0,
            "errors": [],
            "skipped": False,
            "records_inserted": 0,
            "records_updated": 0,
            "records_skipped": 0,
            "gaps_found": 0,
            "gaps_repaired": 0,
            "invalid_found": 0,
            "invalid_repaired": 0,
            "failed_partitions": [],
            "source_usage": {},
            "requested_ranges": [],
            "fallbacks": [],
        }
        if operation != "check":
            sync_result = self._sync(definition.key, partition_key, operation, force)
        errors = list(sync_result.get("errors") or [])
        run_status = _dataset_status(
            {"errors": errors, "records": int(sync_result.get("records") or 0)}
        )
        snapshots = self._check(
            definition,
            partition_key,
            run_id,
            operation,
            local_calendar=operation == "check",
            run_status=run_status,
            run_errors=errors,
        )
        self._db.commit()
        return {
            "checked_partitions": len(snapshots),
            "operation": operation,
            "records": int(sync_result.get("records") or 0),
            "records_inserted": int(sync_result.get("records_inserted") or 0),
            "records_updated": int(sync_result.get("records_updated") or 0),
            "records_skipped": int(sync_result.get("records_skipped") or 0),
            "gaps_found": int(sync_result.get("gaps_found") or 0),
            "gaps_repaired": int(sync_result.get("gaps_repaired") or 0),
            "invalid_found": int(sync_result.get("invalid_found") or 0),
            "invalid_repaired": int(sync_result.get("invalid_repaired") or 0),
            "error_count": len(errors),
            "errors": errors[:20],
            "skipped": bool(sync_result.get("skipped")),
            "failed_partitions": list(sync_result.get("failed_partitions") or []),
            "source_usage": dict(sync_result.get("source_usage") or {}),
            "requested_ranges": list(sync_result.get("requested_ranges") or []),
            "fallbacks": list(sync_result.get("fallbacks") or []),
        }

    def _sync(
        self,
        dataset_key: str,
        partition_key: str | None,
        operation: str,
        force: bool,
    ) -> dict[str, Any]:
        """调用既有服务执行同步、补数或安全重拉。

        与旧实现不同：分区级失败只写入 errors 不中断后续分区；低频数据
        集在未到刷新周期且未 force 时跳过上游拉取（skipped=True）。

        Args:
            dataset_key: 数据集键。
            partition_key: 可选分区键。
            operation: 维护操作。
            force: 是否绕过节流直接拉取上游。

        Returns:
            {records, errors, skipped}。
        """
        from quant_etf_api.services.index_membership_data_service import IndexMembershipDataService
        from quant_etf_api.services.industry_data_service import IndustryDataService
        from quant_etf_api.services.ingest_service import IngestService
        from quant_etf_api.services.stock_data_service import StockDataService

        records = 0
        errors: list[str] = []
        skipped = False
        expected = self._latest_trading_day()
        stats: dict[str, Any] = {
            "records_inserted": 0,
            "records_updated": 0,
            "records_skipped": 0,
            "gaps_found": 0,
            "gaps_repaired": 0,
            "invalid_found": 0,
            "invalid_repaired": 0,
            "failed_partitions": [],
            "source_usage": {},
            "requested_ranges": [],
            "fallbacks": [],
        }

        def _fail(scope: str, exc: Exception) -> None:
            """回滚当前会话并记录分区级失败。"""
            self._db.rollback()
            errors.append(f"{scope}: {type(exc).__name__}: {exc}")
            stats["failed_partitions"].append(scope)

        def _add_records(count: int, *, updated: int = 0) -> None:
            """Accumulate compatibility and detailed write counters."""
            nonlocal records
            records += count
            stats["records_updated"] += updated
            stats["records_inserted"] += max(0, count - updated)

        def _add_source(source: str | None) -> None:
            """Accumulate the selected source count for this operation."""
            if source:
                stats["source_usage"][source] = stats["source_usage"].get(source, 0) + 1

        def _add_fallbacks(service: Any, partition: str | None = None) -> None:
            """Merge client-side source fallback metadata into run metrics."""
            for item in getattr(service, "_last_fallbacks", []):
                fallback = dict(item)
                fallback.setdefault("partition_key", partition)
                stats["fallbacks"].append(fallback)

        if dataset_key == "trading_calendar":
            try:
                calendar = TradingCalendar()
                calendar.refresh()
                trading_days = calendar.get_trading_days_set()
                if trading_days is None:
                    raise RuntimeError("交易日历上游不可用，无法安全更新日历数据")
                existing_dates = {
                    row[0] for row in self._db.query(TradingCalendarModel.trade_date).all()
                }
                if operation == "rebuild":
                    records = self._rebuild_calendar(trading_days, existing_dates)
                else:
                    # 日常同步与补缺只追加新交易日，不删除本地已有日期
                    new_days = sorted(trading_days - existing_dates)
                    for trade_date in new_days:
                        self._db.add(
                            TradingCalendarModel(trade_date=trade_date, is_trading_day=True)
                        )
                    records = len(new_days)
                self._db.commit()
            except Exception as exc:  # noqa: PERF203
                _fail("trading_calendar", exc)
        elif dataset_key == "index_daily_bar":
            service = IngestService(self._db)
            if operation == "repair_gaps":
                try:
                    result = self._repair_index_daily_gaps(partition_key, service, force=force)
                    _add_records(int(result.get("records") or 0))
                    errors.extend(str(error) for error in result.get("errors", []))
                    stats["gaps_found"] += int(result.get("gaps_found") or 0)
                    stats["gaps_repaired"] += int(result.get("upstream_missing_confirmed") or 0)
                    # 非交易日行被删除、仅收盘价日期被确认，都等价于修复了一条异常
                    stats["invalid_repaired"] += int(result.get("non_trading_removed") or 0) + int(
                        result.get("close_only_confirmed") or 0
                    )
                except Exception as exc:  # noqa: PERF203
                    _fail(partition_key or dataset_key, exc)
            else:
                for code in self._partitions(dataset_key) if not partition_key else [partition_key]:
                    try:
                        if operation == "rebuild":
                            _add_records(self._safe_replace_index_bars(service, code))
                            _add_fallbacks(service, code)
                            continue
                        latest = (
                            self._db.query(func.max(IndexDailyBarModel.trade_date))
                            .filter(IndexDailyBarModel.index_code == code)
                            .scalar()
                        )
                        if (
                            latest is not None
                            and latest >= expected
                            and not force
                            and operation == "sync_latest"
                        ):
                            skipped = True
                            continue
                        fetched = service._fetch_and_upsert_index_bars(
                            code, incremental=True, overwrite=False
                        )
                        _add_fallbacks(service, code)
                        _add_records(fetched)
                        _add_source(self._latest_source(IndexDailyBarModel, code))
                    except Exception as exc:  # noqa: PERF203
                        _fail(code, exc)
        elif dataset_key == "index_valuation":
            service = IngestService(self._db)
            if operation == "repair_gaps":
                try:
                    result = self._repair_index_valuation_gaps(partition_key, service, force=force)
                    _add_records(int(result.get("records") or 0))
                    errors.extend(str(error) for error in result.get("errors", []))
                    stats["gaps_found"] += int(result.get("gaps_found") or 0)
                    stats["gaps_repaired"] += int(result.get("upstream_missing_confirmed") or 0)
                except Exception as exc:  # noqa: PERF203
                    _fail(partition_key or dataset_key, exc)
            else:
                for code in self._partitions(dataset_key) if not partition_key else [partition_key]:
                    try:
                        if operation == "rebuild":
                            _add_records(self._safe_replace_index_valuations(service, code))
                            _add_fallbacks(service, code)
                            continue
                        latest = (
                            self._db.query(func.max(IndexValuationModel.trade_date))
                            .filter(IndexValuationModel.index_code == code)
                            .scalar()
                        )
                        if latest is not None and latest >= expected and not force:
                            skipped = True
                            continue
                        fetched = service._fetch_and_upsert_index_valuation(code)
                        _add_fallbacks(service, code)
                        _add_records(fetched)
                        _add_source(self._latest_source(IndexValuationModel, code))
                    except Exception as exc:  # noqa: PERF203
                        _fail(code, exc)
        elif dataset_key == "macro_indicator":
            try:
                service = IngestService(self._db)
                _add_records(
                    service._fetch_and_upsert_macro(
                        overwrite=operation in {"repair_gaps", "rebuild"}
                    )
                )
                _add_fallbacks(service)
            except Exception as exc:  # noqa: PERF203
                _fail("macro_indicator", exc)
        elif dataset_key == "index_membership":
            codes = self._partitions(dataset_key) if not partition_key else [partition_key]
            if operation == "sync_latest" and codes and not force:
                rows = (
                    self._db.query(
                        IndexMemberEventModel.index_code,
                        func.max(IndexMemberEventModel.updated_at),
                    )
                    .filter(IndexMemberEventModel.index_code.in_(codes))
                    .group_by(IndexMemberEventModel.index_code)
                    .all()
                )
                latest_by = dict(rows)
                cutoff = today_cn() - timedelta(days=_LOW_FREQ_REFRESH_DAYS)
                if all(
                    latest_by.get(code) is not None and latest_by[code].date() >= cutoff
                    for code in codes
                ):
                    skipped = True
            if operation == "rebuild":
                service = IndexMembershipDataService(self._db)
                for code in codes:
                    try:
                        service.rebuild_index(code, date(2013, 1, 1), today_cn())
                        stats["source_usage"]["membership"] = (
                            stats["source_usage"].get("membership", 0) + 1
                        )
                        _add_records(1)
                    except Exception as exc:  # noqa: PERF203
                        _fail(code, exc)
            elif operation == "repair_gaps":
                service = IndexMembershipDataService(self._db)
                for code in codes:
                    # 逐指数隔离失败：单只回填异常不中断其余指数
                    try:
                        result = service.backfill_pit(
                            [code], date(2013, 1, 1), today_cn()
                        )
                        _add_records(int(result.get("items", {}).get(code) or 0))
                        errors.extend(str(e) for e in result.get("errors", []))
                    except Exception as exc:  # noqa: PERF203
                        _fail(code, exc)
            elif not skipped:
                try:
                    result = IndexMembershipDataService(self._db).refresh_current_snapshots(codes)
                    _add_records(sum(int(v) for v in result.get("items", {}).values()))
                    errors.extend(str(e) for e in result.get("errors", []))
                except Exception as exc:  # noqa: PERF203
                    _fail("index_membership", exc)
        elif dataset_key == "industry_universe":
            try:
                result = IndustryDataService(self._db).sync_universe()
                _add_records(int(result.get("added") or 0) + int(result.get("updated") or 0))
            except Exception as exc:  # noqa: PERF203
                _fail("industry_universe", exc)
        elif dataset_key == "industry_daily_bar":
            service = IndustryDataService(self._db)
            if operation == "repair_gaps":
                try:
                    result = self._repair_industry_daily_gaps(partition_key, service, force=force)
                    _add_records(int(result.get("records") or 0))
                    errors.extend(str(error) for error in result.get("errors", []))
                    stats["gaps_found"] += int(result.get("gaps_found") or 0)
                    stats["gaps_repaired"] += int(result.get("upstream_missing_confirmed") or 0)
                    stats["invalid_repaired"] += int(result.get("non_trading_removed") or 0)
                except Exception as exc:  # noqa: PERF203
                    _fail(partition_key or dataset_key, exc)
            elif partition_key:
                try:
                    if operation == "rebuild":
                        result = service.rebuild_industry(partition_key)
                        _add_records(int(result.get("upserted_rows") or 0))
                    elif force or self._industry_code_behind(partition_key, expected):
                        result = service.fill_industry(partition_key)
                        _add_records(int(result.get("fetched_rows") or 0))
                    else:
                        skipped = True
                except Exception as exc:  # noqa: PERF203
                    _fail(partition_key, exc)
            elif operation == "rebuild":
                for code in self._partitions(dataset_key):
                    try:
                        result = service.rebuild_industry(code)
                        _add_records(int(result.get("upserted_rows") or 0))
                    except Exception as exc:  # noqa: PERF203
                        _fail(code, exc)
            elif force or self._industry_dataset_behind(expected):
                try:
                    result = service.refresh_industry_bars_incremental()
                    _add_records(int(result.get("records") or 0))
                    errors.extend(str(e) for e in result.get("errors", []))
                except Exception as exc:  # noqa: PERF203
                    _fail("industry_daily_bar", exc)
            else:
                skipped = True
        elif dataset_key == "industry_membership":
            try:
                result = IndustryDataService(self._db).refresh_membership(
                    force=force or operation in {"repair_gaps", "rebuild"}
                )
                if result.get("skipped"):
                    skipped = True
                _add_records(int(result.get("total") or 0))
            except Exception as exc:  # noqa: PERF203
                _fail("industry_membership", exc)
        elif dataset_key == "stock_universe":
            if not force:
                newest = self._db.query(func.max(StockUniverseModel.updated_at)).scalar()
                cutoff = today_cn() - timedelta(days=_LOW_FREQ_REFRESH_DAYS)
                if newest is not None and newest.date() >= cutoff:
                    skipped = True
            if not skipped:
                try:
                    result = StockDataService(self._db).sync_universe()
                    _add_records(int(result.get("added") or 0) + int(result.get("updated") or 0))
                except Exception as exc:  # noqa: PERF203
                    _fail("stock_universe", exc)
        elif dataset_key in {
            "stock_daily_close",
            "stock_daily_basic",
            "stock_moneyflow",
        }:
            service = StockDataService(self._db)
            if operation == "repair_gaps":
                try:
                    result = self._repair_stock_daily_gaps(
                        dataset_key, partition_key, service, force=force
                    )
                    _add_records(int(result.get("records") or 0))
                    errors.extend(str(error) for error in result.get("errors", []))
                    stats["gaps_found"] += int(result.get("gaps_found") or 0)
                    # 已确认的上游空档不再属于待修复异常，等价于从质量缺口中消除。
                    stats["gaps_repaired"] += int(result.get("upstream_missing_confirmed") or 0)
                except Exception as exc:  # noqa: PERF203
                    _fail(partition_key or dataset_key, exc)
            elif partition_key:
                try:
                    if operation == "rebuild":
                        result = service.rebuild_stock(partition_key, datasets=[dataset_key])
                        _add_records(int(result.get("upserted_total") or 0))
                        verification = self._repair_stock_daily_gaps(
                            dataset_key, partition_key, service, force=True
                        )
                        _add_records(int(verification.get("records") or 0))
                        errors.extend(str(error) for error in verification.get("errors", []))
                        stats["gaps_found"] += int(verification.get("gaps_found") or 0)
                        stats["gaps_repaired"] += int(
                            verification.get("upstream_missing_confirmed") or 0
                        )
                    else:
                        result = service.fill_stock(partition_key, datasets=[dataset_key])
                        _add_records(int(result.get("upserted_total") or 0))
                except Exception as exc:  # noqa: PERF203
                    _fail(partition_key, exc)
            else:
                try:
                    if operation == "rebuild":
                        # 全量重建属于显式高风险操作，由页面确认令牌保护。
                        result = service.sync_range(
                            _STOCK_TUSHARE_EPOCH,
                            expected,
                            datasets=[dataset_key],
                            force=True,
                        )
                    else:
                        result = service.sync_missing_recent(datasets=[dataset_key])
                    _add_records(int(result["records"].get(dataset_key) or 0))
                    if operation == "rebuild":
                        verification = self._repair_stock_daily_gaps(
                            dataset_key, None, service, force=True
                        )
                        _add_records(int(verification.get("records") or 0))
                        errors.extend(str(error) for error in verification.get("errors", []))
                        stats["gaps_found"] += int(verification.get("gaps_found") or 0)
                        stats["gaps_repaired"] += int(
                            verification.get("upstream_missing_confirmed") or 0
                        )
                except Exception as exc:  # noqa: PERF203
                    _fail(dataset_key, exc)
        else:
            raise ValueError(f"未实现数据集同步: {dataset_key}")
        stats["failed_partitions"] = sorted(
            set(stats["failed_partitions"])
            | {error.split(":", 1)[0] for error in errors if ":" in error}
        )
        if dataset_key in {
            "index_daily_bar",
            "index_valuation",
            "industry_daily_bar",
            "stock_daily_close",
            "stock_daily_basic",
            "stock_moneyflow",
            "macro_indicator",
        }:
            # 个股三张大表的来源在本轮任务中固定为 Tushare；若再对整表
            # GROUP BY source 会额外扫描千万级历史行，且只为运行指标服务。
            if dataset_key in _STOCK_DAILY_DATASETS:
                stats["source_usage"] = {"tushare": records} if records else {}
            else:
                stats["source_usage"] = self._source_counts(dataset_key, partition_key)
            stats["requested_ranges"] = self._requested_ranges(
                dataset_key, partition_key, expected, operation
            )
        stats.update({"records": records, "errors": errors, "skipped": skipped})
        stats["records_skipped"] += 1 if skipped else 0
        return stats

    def _rebuild_calendar(self, upstream_days: set[date], existing_dates: set[date]) -> int:
        """在上游覆盖范围内用上游交易日集合安全替换本地日历。

        先做完整性校验；本地有、上游没有的日期视为错误日期删除，上游新增的
        日期补入。删除数量超过阈值时视为上游异常而中止，保留本地日历。

        Args:
            upstream_days: 上游交易日集合。
            existing_dates: 本地 trading_calendar 已有日期。

        Returns:
            新增与删除的日期总数。

        Raises:
            TradingCalendarUnavailableError: 上游日历未通过完整性校验时抛出。
            RuntimeError: 待删除日期超过安全阈值时抛出。
        """
        validate_calendar_completeness(upstream_days)
        start, end = min(upstream_days), max(upstream_days)
        removed = sorted(
            day for day in existing_dates if start <= day <= end and day not in upstream_days
        )
        if len(removed) > _CALENDAR_REBUILD_MAX_REMOVALS:
            raise RuntimeError(
                f"上游日历与本地相差 {len(removed)} 个交易日（超过安全阈值 "
                f"{_CALENDAR_REBUILD_MAX_REMOVALS}），已中止重建并保留本地日历："
                f"{[day.isoformat() for day in removed[:10]]}"
            )
        if removed:
            self._db.query(TradingCalendarModel).filter(
                TradingCalendarModel.trade_date.in_(removed)
            ).delete(synchronize_session=False)
            logger.warning("日历重建删除本地错误交易日: %s", [d.isoformat() for d in removed])
        added = sorted(upstream_days - existing_dates)
        for trade_date in added:
            self._db.add(TradingCalendarModel(trade_date=trade_date, is_trading_day=True))
        return len(added) + len(removed)

    def _latest_source(self, model: Any, partition_key: str) -> str | None:
        """Return the latest persisted source for a partition, if available."""
        date_column = next(
            (
                getattr(model, name, None)
                for name in ("trade_date", "period_date", "updated_at", "fetched_at")
                if getattr(model, name, None) is not None
            ),
            None,
        )
        partition_column = next(
            (
                getattr(model, name, None)
                for name in ("index_code", "industry_code", "stock_code", "indicator_code")
                if getattr(model, name, None) is not None
            ),
            None,
        )
        if date_column is None or partition_column is None:
            return None
        row = (
            self._db.query(model.source)
            .filter(partition_column == partition_key)
            .order_by(date_column.desc())
            .first()
        )
        return row[0] if row else None

    def _source_counts(self, dataset_key: str, partition_key: str | None) -> dict[str, int]:
        """汇总目标数据集当前落库记录的来源分布。"""
        model, partition_column, _, source_column = self._model_columns(dataset_key)
        if source_column is None:
            return {}
        query = self._db.query(source_column, func.count()).select_from(model)
        if partition_column is not None and partition_key:
            query = query.filter(partition_column == partition_key)
        rows = query.group_by(source_column).all()
        return {str(source or "unknown"): int(count) for source, count in rows}

    def _partition_missing_count(self, dataset_key: str, partition_key: str) -> int:
        """Return the current exact missing-date count for one daily partition."""
        definition = self._definition(dataset_key)
        payload = self._inspect_many(definition, [partition_key]).get(partition_key, {})
        return int(payload.get("missing_count") or 0)

    def _partition_invalid_count(self, dataset_key: str, partition_key: str) -> int:
        """Return the current invalid-row count for one partition."""
        definition = self._definition(dataset_key)
        payload = self._inspect_many(definition, [partition_key]).get(partition_key, {})
        return int(payload.get("invalid_count") or 0)

    def _requested_ranges(
        self,
        dataset_key: str,
        partition_key: str | None,
        expected: date,
        operation: str,
    ) -> list[dict[str, Any]]:
        """Return compact request-range metadata for a maintenance run."""
        if dataset_key not in {
            "index_daily_bar",
            "index_valuation",
            "industry_daily_bar",
            "stock_daily_close",
            "stock_daily_basic",
            "stock_moneyflow",
            "macro_indicator",
        }:
            return []
        keys = [partition_key] if partition_key else self._partitions(dataset_key)
        # 运行指标最多返回 2,000 个分区；聚合和来源查询也只处理这部分，
        # 避免全市场操作为不会返回的分区额外扫描大表。
        display_keys = keys[:2000]
        model, partition_column, date_column, _ = self._model_columns(dataset_key)
        counts: dict[str, int] = {}
        earliest: dict[str, date] = {}
        source_by_key: dict[str, str | None] = {}
        if partition_column is not None and display_keys:
            rows = (
                self._db.query(
                    partition_column,
                    func.count(),
                    func.min(cast(date_column, Date)),
                )
                .filter(partition_column.in_(display_keys))
                .group_by(partition_column)
                .all()
            )
            counts = {str(key): int(count) for key, count, _ in rows}
            earliest = {str(key): value for key, _, value in rows if value is not None}
            latest_by_key = (
                self._db.query(
                    partition_column.label("partition_key"),
                    func.max(date_column).label("latest_date"),
                )
                .filter(partition_column.in_(display_keys))
                .group_by(partition_column)
                .subquery()
            )
            source_rows = (
                self._db.query(partition_column, model.source)
                .join(
                    latest_by_key,
                    and_(
                        partition_column == latest_by_key.c.partition_key,
                        date_column == latest_by_key.c.latest_date,
                    ),
                )
                .all()
            )
            source_by_key = {str(key): source for key, source in source_rows}
        return [
            {
                "partition_key": key,
                "source": source_by_key.get(key),
                "start": (
                    earliest.get(key).isoformat()
                    if operation == "sync_latest" and earliest.get(key) is not None
                    else "2013-01-01"
                ),
                "end": expected.isoformat(),
                "records": counts.get(key, 0),
                "operation": operation,
            }
            for key in display_keys
        ]

    def _industry_code_behind(self, industry_code: str, expected: date) -> bool:
        """判断单行业日线是否落后于目标交易日。"""
        latest = (
            self._db.query(func.max(IndustryDailyBarModel.trade_date))
            .filter(IndustryDailyBarModel.industry_code == industry_code)
            .scalar()
        )
        return latest is None or latest < expected

    def _industry_dataset_behind(self, expected: date) -> bool:
        """判断活跃行业集合中是否存在落后于目标交易日的行业。"""
        codes = [
            row[0]
            for row in self._db.query(IndustryUniverseModel.industry_code)
            .filter(IndustryUniverseModel.is_active.is_(True))
            .all()
        ]
        if not codes:
            return False
        latest_rows = (
            self._db.query(
                IndustryDailyBarModel.industry_code,
                func.max(IndustryDailyBarModel.trade_date),
            )
            .filter(IndustryDailyBarModel.industry_code.in_(codes))
            .group_by(IndustryDailyBarModel.industry_code)
            .all()
        )
        latest_by = dict(latest_rows)
        return any(latest_by.get(code) is None or latest_by[code] < expected for code in codes)

    def _ensure_replacement_span(
        self,
        scope: str,
        existing_count: int,
        existing_min: date | None,
        existing_max: date | None,
        fetched_dates: set[date],
    ) -> None:
        """重拉前校验新抓取数据范围，防止上游降级时删除完整历史。

        Args:
            scope: 分区标识（用于错误信息）。
            existing_count: 库内现有记录数。
            existing_min: 库内最早日期。
            existing_max: 库内最晚日期。
            fetched_dates: 新抓取数据的交易日集合。

        Raises:
            RuntimeError: 新数据明显少于或未覆盖现有数据时抛出。
        """
        if existing_count <= 0 or not fetched_dates:
            return
        missing = existing_count - len(fetched_dates)
        if missing > max(1, int(existing_count * 0.01)):
            raise RuntimeError(
                f"{scope} 新抓取数据（{len(fetched_dates)} 天）明显少于库内现有数据"
                f"（{existing_count} 天），已中止替换并保留旧数据"
            )
        if existing_min is not None and (
            existing_min < min(fetched_dates) or existing_max > max(fetched_dates)
        ):
            raise RuntimeError(
                f"{scope} 新抓取数据日期范围未覆盖库内现有数据，已中止替换并保留旧数据"
            )

    def _safe_replace_index_bars(self, service: Any, code: str) -> int:
        """安全全量重拉单指数日线：先校验覆盖范围再删除写回。

        与增量同步一致，写回前剔除落在非交易日的行（上游基日/假日行情），
        否则重建会把这些行重新带回库内。
        """
        bars, source = service._fetch_index_daily_multi_source(code)
        bars, dropped = service._drop_non_trading_bars(bars)
        if dropped:
            logger.warning(
                "重建剔除非交易日行情: index_code=%s 条数=%d 日期=%s",
                code,
                len(dropped),
                [str(b.trade_date) for b in dropped[:5]],
            )
        if not bars:
            raise RuntimeError(f"指数 {code} 未获取到可替换日线")
        existing_count, existing_min, existing_max = (
            self._db.query(
                func.count(),
                func.min(IndexDailyBarModel.trade_date),
                func.max(IndexDailyBarModel.trade_date),
            )
            .filter(IndexDailyBarModel.index_code == code)
            .one()
        )
        self._ensure_replacement_span(
            f"指数 {code}",
            int(existing_count or 0),
            existing_min,
            existing_max,
            {b.trade_date for b in bars},
        )
        self._db.query(IndexDailyBarModel).filter(IndexDailyBarModel.index_code == code).delete(
            synchronize_session=False
        )
        service._insert_index_bars(code, bars, source=source)
        self._db.commit()
        return len(bars)

    def _safe_replace_index_valuations(self, service: Any, code: str) -> int:
        """安全全量重拉单指数估值（Tushare 优先）：先校验覆盖范围再删除写回。"""
        values = service._fetch_index_valuation_preferred(code)
        if not values:
            raise RuntimeError(f"指数 {code} 未获取到可替换估值，已保留旧数据")
        existing_count, existing_min, existing_max = (
            self._db.query(
                func.count(),
                func.min(IndexValuationModel.trade_date),
                func.max(IndexValuationModel.trade_date),
            )
            .filter(IndexValuationModel.index_code == code)
            .one()
        )
        self._ensure_replacement_span(
            f"指数 {code}",
            int(existing_count or 0),
            existing_min,
            existing_max,
            {v.trade_date for v in values},
        )
        self._db.query(IndexValuationModel).filter(IndexValuationModel.index_code == code).delete(
            synchronize_session=False
        )
        service._insert_index_valuations(code, values)
        self._db.commit()
        return len(values)

    def _check(
        self,
        definition: DataSetDefinition,
        partition_key: str | None,
        run_id: str,
        operation: str,
        *,
        local_calendar: bool = False,
        run_status: str = "success",
        run_errors: list[str] | None = None,
    ) -> list[DataHealthSnapshotModel]:
        """重算指定数据集范围的健康快照并持久化。

        无分区数据集（partition_label is None）的检查结果本身就是汇总行，
        直接以 "" 分区保存，不再二次聚合覆盖；只有分区数据集在整集检查时
        才重算汇总行，单分区检查只更新对应分区。

        同步阶段的失败会如实写回：错误前缀命中分区键的分区标记为 failed，
        汇总行记录数据集级运行状态（success/partial_success/failed）。
        整集检查时，已不在当前可维护分区列表中的快照行（退市证券、移除的
        指数）会被删除，不再拖累汇总状态；只删快照，不删行情数据。

        Args:
            definition: 数据集定义。
            partition_key: 可选单分区。
            run_id: 父运行 ID。
            operation: 维护操作。
            local_calendar: 是否只用本地日历快照。
            run_status: 同步阶段的数据集级运行状态。
            run_errors: 同步阶段收集的错误（形如 "分区: 错误信息"）。

        Returns:
            本次写入的分区快照。
        """
        partitions = [partition_key] if partition_key else self._partitions(definition.key)
        exact_missing = not (
            operation == "sync_latest"
            and definition.key
            in {"stock_daily_close", "stock_daily_basic", "stock_moneyflow"}
        )
        existing_rows = (
            self._db.query(DataHealthSnapshotModel)
            .filter(DataHealthSnapshotModel.dataset_key == definition.key)
            .all()
        )
        existing = {row.partition_key: row for row in existing_rows}
        if partition_key is None and definition.partition_label is not None and partitions:
            active = set(partitions)
            for key in [key for key in existing if key and key not in active]:
                self._db.delete(existing.pop(key))
        errors = list(run_errors or [])
        partition_failures: dict[str, str] = {}
        for error in errors:
            scope, _, message = error.partition(":")
            # 修复路径的错误形如 "000903/akshare: ..."，分区键取斜杠前的部分
            scope = scope.split("/", 1)[0].strip()
            if scope in existing or scope in partitions:
                partition_failures.setdefault(scope, message.strip() or error)
        upstream_ranges: dict[str, list[dict[str, Any]]] | None = None
        close_only_ranges: dict[str, list[dict[str, Any]]] = {}
        if definition.key in _UPSTREAM_EXEMPT_DAILY_DATASETS:
            # "上游仅收盘价"区间与上游缺失区间共用存储，但只用于豁免字段异常；
            # 不能交给缺口逻辑，否则会因日期"并不缺失"而被当作过期豁免清掉。
            upstream_ranges = {}
            for key, row in existing.items():
                if not key:
                    continue
                ranges = list(getattr(row, "upstream_missing_ranges", None) or [])
                close_only = [
                    item
                    for item in ranges
                    if isinstance(item, dict) and item.get("source") == _CLOSE_ONLY_SOURCE
                ]
                upstream_ranges[key] = [item for item in ranges if item not in close_only]
                if close_only:
                    close_only_ranges[key] = close_only
        inspect_kwargs: dict[str, Any] = {}
        if upstream_ranges:
            inspect_kwargs["upstream_missing_by_partition"] = upstream_ranges
        if close_only_ranges:
            inspect_kwargs["close_only_by_partition"] = close_only_ranges
        if local_calendar:
            payloads = self._inspect_many(definition, partitions, local_calendar=True, **inspect_kwargs)
        elif not exact_missing:
            # 日常全局同步只负责补齐近期数据。对每个历史缺口证券再做
            # count + sample 两次日历反连接会造成数万次查询；完整精查保留给
            # 手动“检查”和“修复缺口”操作。
            payloads = self._inspect_many(
                definition,
                partitions,
                exact_missing=False,
                **inspect_kwargs,
            )
        else:
            # 保留旧的无关键字调用形态，便于已有测试和扩展服务替换检查器。
            payloads = self._inspect_many(definition, partitions, **inspect_kwargs)
        result: list[DataHealthSnapshotModel] = []
        for key, payload in payloads.items():
            if close_only_ranges.get(key) and "upstream_missing_ranges" in payload:
                payload = {
                    **payload,
                    "upstream_missing_ranges": list(payload["upstream_missing_ranges"] or [])
                    + close_only_ranges[key],
                }
            if key:
                failure = partition_failures.get(key)
                status, last_error = ("failed", failure) if failure else ("success", None)
            else:
                # 无分区数据集的 "" 行就是汇总行，直接反映数据集级运行状态
                status = run_status
                last_error = "; ".join(errors[:5]) if errors else None
            snapshot = self._save_snapshot(
                definition,
                key,
                run_id,
                operation,
                payload,
                existing.get(key),
                run_status=status,
                last_error=last_error,
            )
            result.append(snapshot)
        self._db.flush()
        if definition.partition_label is None or partition_key is not None:
            return result
        aggregate_rows = (
            self._db.query(DataHealthSnapshotModel)
            .filter(
                DataHealthSnapshotModel.dataset_key == definition.key,
                DataHealthSnapshotModel.partition_key != "",
            )
            .all()
        )
        self._aggregate(
            definition,
            aggregate_rows,
            run_id,
            operation,
            existing.get(""),
            run_status=run_status,
            run_errors=errors,
        )
        return result

    def _partitions(self, dataset_key: str) -> list[str]:
        """返回数据集当前可维护分区列表。"""
        if dataset_key in {"index_daily_bar", "index_valuation", "index_membership"}:
            return [
                r[0]
                for r in self._db.query(BenchmarkIndexModel.index_code)
                .filter(BenchmarkIndexModel.is_active.is_(True))
                .all()
            ]
        if dataset_key == "macro_indicator":
            return [
                r[0] for r in self._db.query(MacroIndicatorModel.indicator_code).distinct().all()
            ] or ["cpi", "pmi", "lpr1y", "lpr5y"]
        if dataset_key == "industry_daily_bar":
            return [
                r[0]
                for r in self._db.query(IndustryUniverseModel.industry_code)
                .filter(IndustryUniverseModel.is_active.is_(True))
                .all()
            ]
        if dataset_key == "industry_membership":
            return [r[0] for r in self._db.query(IndustryUniverseModel.industry_code).all()]
        if dataset_key in {
            "stock_daily_close",
            "stock_daily_basic",
            "stock_moneyflow",
        }:
            return [
                r[0]
                for r in self._db.query(StockUniverseModel.stock_code)
                .filter(StockUniverseModel.is_active.is_(True))
                .all()
            ]
        return []

    def _inspect_many(
        self,
        definition: DataSetDefinition,
        partitions: list[str],
        *,
        local_calendar: bool = False,
        exact_missing: bool = True,
        upstream_missing_by_partition: dict[str, list[dict[str, Any]]] | None = None,
        close_only_by_partition: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """用聚合 SQL 一次计算一个数据集的所有分区健康指标。

        不把历史行情 ORM 实体加载到 Python。个股日线等大表只扫描一次，
        由数据库完成行数、日期边界和字段异常计数。``exact_missing=False``
        用于日常同步：保留聚合缺口计数，但跳过每个问题分区的精确日历反
        连接；手动检查与修复仍使用精确模式。

        指数/行业日线额外统计：非交易日行（计入字段异常）、成交量为 0 的行
        （只提示不影响状态），以及落在"上游仅收盘价"确认区间内、仅因开高低
        缺失而判错的行（从硬错误中豁免，改计为告警）。
        """
        model, partition_column, date_column, source_column = self._model_columns(definition.key)
        date_expression = cast(date_column, Date) if date_column is not None else None
        error_condition, warning_condition = self._quality_conditions(definition.key)
        columns: list[Any] = [
            func.count().label("record_count"),
            func.coalesce(func.sum(case((error_condition, 1), else_=0)), 0).label("error_count"),
            func.coalesce(func.sum(case((warning_condition, 1), else_=0)), 0).label(
                "warning_count"
            ),
        ]
        if date_expression is not None:
            columns.extend(
                [
                    func.min(date_expression).label("earliest_date"),
                    func.max(date_expression).label("latest_date"),
                ]
            )
        is_bar_dataset = definition.key in {"index_daily_bar", "industry_daily_bar"}
        if is_bar_dataset:
            # 成交量为 0 多为来源不提供成交量（如 AkShare 历史），只作提示
            columns.append(
                func.coalesce(func.sum(case((model.volume == 0, 1), else_=0)), 0).label(
                    "volume_zero_count"
                )
            )
        if source_column is not None and definition.key in _STOCK_DAILY_DATASETS:
            # 个股三张大表固定为 Tushare 单一来源，取最新行来源需再扫一次千万级
            # 历史，这里沿用聚合最大值即可得到同一结论。
            columns.append(func.max(source_column).label("source_name"))

        if definition.partition_label is None:
            row = self._db.query(*columns).select_from(model).one()
            raw_rows = {"": row}
        else:
            query = self._db.query(partition_column.label("partition_key"), *columns).select_from(
                model
            )
            if partitions:
                query = query.filter(partition_column.in_(partitions))
            raw_rows = {
                str(row.partition_key): row for row in query.group_by(partition_column).all()
            }

        keys = [""] if definition.partition_label is None else partitions
        expected = self._expected_date(definition.key, local_calendar=local_calendar)
        calendar_days = (
            self._calendar_days_until(expected, local_calendar=local_calendar)
            if self._is_daily(definition.key)
            else []
        )
        daily_exact_missing = (
            self._daily_exact_missing_summaries(
                definition.key,
                partitions,
                calendar_days,
                upstream_missing_by_partition or {},
            )
            if definition.key in _UPSTREAM_EXEMPT_DAILY_DATASETS and exact_missing
            else None
        )
        latest_sources = (
            self._latest_sources(definition.key, partitions)
            if source_column is not None and definition.key not in _STOCK_DAILY_DATASETS
            else {}
        )
        non_trading_counts = (
            self._non_trading_counts(definition.key, partitions, calendar_days)
            if is_bar_dataset
            else {}
        )
        close_only_exempt = (
            self._close_only_exempt_counts(
                definition.key, close_only_by_partition or {}, calendar_days
            )
            if is_bar_dataset
            else {}
        )
        partition_names = self._partition_names(definition.key, keys)
        result: dict[str, dict[str, Any]] = {}
        for key in keys:
            row = raw_rows.get(key)
            record_count = int(row.record_count) if row is not None else 0
            earliest = (
                row.earliest_date if row is not None and date_expression is not None else None
            )
            latest = row.latest_date if row is not None and date_expression is not None else None
            raw_errors = int(row.error_count) if row is not None else 0
            close_only_count = min(close_only_exempt.get(key, 0), raw_errors)
            non_trading_count = non_trading_counts.get(key, 0)
            hard_errors = raw_errors - close_only_count + non_trading_count
            warnings = (int(row.warning_count) if row is not None else 0) + close_only_count
            volume_zero_count = (
                int(row.volume_zero_count) if row is not None and is_bar_dataset else 0
            )
            range_start, range_end = self._coverage_bounds(
                definition.key, earliest, latest, expected
            )
            partition_calendar_days = (
                [day for day in calendar_days if range_end is None or day <= range_end]
                if range_end is not None
                else []
            )
            missing = self._missing_count_from_bounds(
                range_start,
                record_count,
                partition_calendar_days,
                valid_count=max(0, record_count - hard_errors),
            )
            missing_sample: list[str] = []
            upstream_missing_ranges: list[dict[str, Any]] = []
            if daily_exact_missing is not None and key:
                exact = daily_exact_missing.get(key, {})
                missing = int(exact.get("missing_count") or 0)
                missing_sample = list(exact.get("missing_sample") or [])
                upstream_missing_ranges = list(exact.get("upstream_missing_ranges") or [])
            elif self._is_daily(definition.key) and key and missing and exact_missing:
                raw_missing_dates = self._exact_missing_date_values(
                    definition.key, key, range_start, range_end
                )
                exempt_dates = self._upstream_exempt_dates(
                    raw_missing_dates,
                    (upstream_missing_by_partition or {}).get(key, []),
                )
                unresolved_dates = [day for day in raw_missing_dates if day not in exempt_dates]
                missing = len(unresolved_dates)
                missing_sample = [day.isoformat() for day in unresolved_dates[:20]]
                upstream_missing_ranges = self._remaining_upstream_missing_ranges(
                    exempt_dates,
                    calendar_days,
                    (upstream_missing_by_partition or {}).get(key, []),
                )
            elif definition.key in _UPSTREAM_EXEMPT_DAILY_DATASETS and key:
                # 日常同步不做逐证券反连接；以已确认区间的交易日数修正聚合
                # 缺口，并原样保留豁免，下一次精查会清理已被本地补入的日期。
                known_dates = self._range_dates(
                    (upstream_missing_by_partition or {}).get(key, []), calendar_days
                )
                known_dates = {
                    day
                    for day in known_dates
                    if (range_start is None or day >= range_start)
                    and (range_end is None or day <= range_end)
                }
                missing = max(0, missing - len(known_dates))
                upstream_missing_ranges = self._remaining_upstream_missing_ranges(
                    known_dates,
                    calendar_days,
                    (upstream_missing_by_partition or {}).get(key, []),
                )
            status = self._health_status(
                definition.key, key, record_count, latest, expected, missing, hard_errors, warnings
            )
            reasons: list[str] = []
            if record_count == 0:
                reasons.append("empty")
            if latest is not None and expected is not None and latest < expected:
                reasons.append("stale")
            if missing:
                reasons.append("missing_dates")
            if hard_errors:
                reasons.append("invalid_values")
            if non_trading_count:
                reasons.append("non_trading_rows")
            if warnings:
                reasons.append("warnings")
            issues: dict[str, Any] = {
                "missing_count": missing,
                "error_count": hard_errors,
                "warning_count": warnings,
                "reasons": reasons,
            }
            if non_trading_count:
                issues["non_trading_count"] = non_trading_count
            if close_only_count:
                issues["close_only_count"] = close_only_count
            if volume_zero_count:
                # 仅提示：不进入 reasons，也不影响健康状态
                issues["volume_zero_count"] = volume_zero_count
            if missing_sample:
                issues["missing_dates_sample"] = missing_sample
                issues["missing_dates_truncated"] = missing > len(missing_sample)
            if definition.key in _STOCK_DAILY_DATASETS:
                source_name = row.source_name if row is not None else None
            else:
                source_name = latest_sources.get(key)
            result[key] = {
                "partition_name": partition_names.get(key),
                "health_status": status,
                "source_name": source_name,
                "earliest_date": earliest,
                "latest_date": latest,
                "expected_date": expected,
                "record_count": record_count,
                "missing_count": missing,
                "invalid_count": hard_errors,
                "upstream_missing_ranges": upstream_missing_ranges,
                "issue_summary": issues
                if any(issues.values()) or status in {"error", "warning", "unknown"}
                else None,
            }
        return result

    def _latest_sources(self, dataset_key: str, partitions: list[str]) -> dict[str, str | None]:
        """返回每个分区最新日期那一行的来源（即最近一次实际抓取的来源）。

        Args:
            dataset_key: 数据集键。
            partitions: 分区列表；无分区数据集返回 {"": 来源}。

        Returns:
            {分区键: 来源}，无数据的分区不出现。
        """
        model, partition_column, date_column, source_column = self._model_columns(dataset_key)
        if source_column is None or date_column is None:
            return {}
        if partition_column is None:
            row = self._db.query(source_column).order_by(date_column.desc()).first()
            return {"": row[0]} if row else {}
        latest_by_key = self._db.query(
            partition_column.label("partition_key"),
            func.max(date_column).label("latest_date"),
        )
        if partitions:
            latest_by_key = latest_by_key.filter(partition_column.in_(partitions))
        latest_subquery = latest_by_key.group_by(partition_column).subquery()
        rows = (
            self._db.query(partition_column, func.max(source_column))
            .join(
                latest_subquery,
                and_(
                    partition_column == latest_subquery.c.partition_key,
                    date_column == latest_subquery.c.latest_date,
                ),
            )
            .group_by(partition_column)
            .all()
        )
        return {str(key): source for key, source in rows}

    def _non_trading_counts(
        self, dataset_key: str, partitions: list[str], calendar_days: list[date]
    ) -> dict[str, int]:
        """统计指数/行业日线中落在非交易日的行数（只看本地日历覆盖范围内）。"""
        if not calendar_days:
            return {}
        model, partition_column, date_column, _ = self._model_columns(dataset_key)
        is_trading = (
            select(TradingCalendarModel.trade_date)
            .where(
                TradingCalendarModel.trade_date == date_column,
                TradingCalendarModel.is_trading_day.is_(True),
            )
            .exists()
        )
        query = self._db.query(partition_column, func.count()).filter(
            date_column <= calendar_days[-1], ~is_trading
        )
        if partitions:
            query = query.filter(partition_column.in_(partitions))
        return {str(key): int(count) for key, count in query.group_by(partition_column).all()}

    def _close_only_exempt_counts(
        self,
        dataset_key: str,
        close_only_by_partition: dict[str, list[dict[str, Any]]],
        calendar_days: list[date],
    ) -> dict[str, int]:
        """统计落在"上游仅收盘价"确认区间内、仅因开高低缺失判错的行数。"""
        ranges_by_key = {key: ranges for key, ranges in close_only_by_partition.items() if ranges}
        if not ranges_by_key:
            return {}
        _, partition_column, date_column, _ = self._model_columns(dataset_key)
        dates_by_key = {
            key: self._range_dates(ranges, calendar_days) for key, ranges in ranges_by_key.items()
        }
        rows = (
            self._db.query(partition_column, date_column)
            .filter(
                partition_column.in_(list(ranges_by_key)),
                self._ohlc_only_error_condition(dataset_key),
            )
            .all()
        )
        counts: dict[str, int] = {}
        for key, trade_date in rows:
            if trade_date in dates_by_key.get(str(key), set()):
                counts[str(key)] = counts.get(str(key), 0) + 1
        return counts

    @staticmethod
    def _coverage_bounds(
        dataset_key: str,
        earliest: date | None,
        latest: date | None,
        expected: date | None,
    ) -> tuple[date | None, date | None]:
        """返回用于连续性检查的确认覆盖区间。

        个股日频接口的实际历史覆盖并不总从证券上市日开始，也可能早于
        最近交易日结束。因而只在该证券、该接口已经确认有数据的最早至最晚
        日期内检查连续性；最新日期是否落后于目标交易日仍由 freshness 规则
        单独标记为 stale，避免将上游未覆盖区间误报成缺口。
        """
        if dataset_key in {
            "stock_daily_close",
            "stock_daily_basic",
            "stock_moneyflow",
        }:
            return earliest, latest
        return earliest, expected

    def _exact_missing_dates(
        self,
        dataset_key: str,
        partition_key: str,
        earliest: date | None,
        expected: date | None,
        fallback_count: int,
    ) -> tuple[int, list[str]]:
        """Use a calendar anti-join to count and sample real missing dates.

        The initial aggregate count is retained as a cheap fallback for empty
        partitions. For populated partitions this query excludes non-trading
        dates and avoids loading historical market rows into Python.
        """
        if earliest is None or expected is None:
            return fallback_count, []
        dates = self._exact_missing_date_values(dataset_key, partition_key, earliest, expected)
        if dates:
            return len(dates), [day.isoformat() for day in dates[:20]]
        return 0, []

    def _exact_missing_date_values(
        self,
        dataset_key: str,
        partition_key: str,
        earliest: date | None,
        expected: date | None,
    ) -> list[date]:
        """Return exact uncovered trading dates for one populated partition."""
        if earliest is None or expected is None:
            return []
        model, partition_column, date_column, _ = self._model_columns(dataset_key)
        if partition_column is None or date_column is None:
            return []
        join_condition = and_(
            partition_column == partition_key,
            date_column == TradingCalendarModel.trade_date,
        )
        base = (
            self._db.query(TradingCalendarModel.trade_date)
            .select_from(TradingCalendarModel)
            .outerjoin(model, join_condition)
            .filter(
                TradingCalendarModel.is_trading_day.is_(True),
                TradingCalendarModel.trade_date >= earliest,
                TradingCalendarModel.trade_date <= expected,
                getattr(model, "id").is_(None),
            )
        )
        return [row[0] for row in base.order_by(TradingCalendarModel.trade_date.asc()).all()]

    @staticmethod
    def _range_dates(ranges: list[dict[str, Any]], calendar_days: list[date]) -> set[date]:
        """Expand persisted inclusive trade-date ranges against the official calendar."""
        result: set[date] = set()
        for item in ranges:
            if not isinstance(item, dict):
                continue
            try:
                start = date.fromisoformat(str(item["start_date"]))
                end = date.fromisoformat(str(item["end_date"]))
            except (KeyError, TypeError, ValueError):
                continue
            result.update(day for day in calendar_days if start <= day <= end)
        return result

    def _upstream_exempt_dates(
        self, missing_dates: list[date], ranges: list[dict[str, Any]]
    ) -> set[date]:
        """Keep only persisted exemptions still absent from the local table."""
        if not missing_dates:
            return set()
        return set(missing_dates) & self._range_dates(ranges, missing_dates)

    @staticmethod
    def _compress_upstream_missing_ranges(
        dates: set[date],
        calendar_days: list[date],
        existing: list[dict[str, Any]],
        *,
        confirmed_at: str | None = None,
        source: str = "tushare",
    ) -> list[dict[str, Any]]:
        """Store exempt dates as contiguous trading-day ranges, retaining confirmation time."""
        if not dates:
            return []
        positions = {day: index for index, day in enumerate(calendar_days)}
        ordered = sorted(day for day in dates if day in positions)
        if not ordered:
            return []
        confirmed_at = confirmed_at or next(
            (
                value
                for item in existing
                if isinstance(item, dict)
                for value in [item.get("confirmed_at")]
                if isinstance(value, str)
            ),
            utcnow().replace(microsecond=0).isoformat() + "Z",
        )
        groups: list[list[date]] = [[ordered[0]]]
        for day in ordered[1:]:
            if positions[day] == positions[groups[-1][-1]] + 1:
                groups[-1].append(day)
            else:
                groups.append([day])
        return [
            {
                "start_date": group[0].isoformat(),
                "end_date": group[-1].isoformat(),
                "trading_day_count": len(group),
                "source": source,
                "confirmed_at": confirmed_at,
            }
            for group in groups
        ]

    def _remaining_upstream_missing_ranges(
        self,
        missing_dates: set[date],
        calendar_days: list[date],
        existing: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """保留仍未落库的豁免日期，并按原验证来源分别压缩。"""
        by_source: dict[str, list[dict[str, Any]]] = {}
        for item in existing:
            if isinstance(item, dict):
                by_source.setdefault(str(item.get("source") or "unknown"), []).append(item)
        result: list[dict[str, Any]] = []
        for source, ranges in by_source.items():
            retained = missing_dates & self._range_dates(ranges, calendar_days)
            result.extend(
                self._compress_upstream_missing_ranges(
                    retained, calendar_days, ranges, source=source
                )
            )
        return result

    def _stock_gap_targets_for_repair(
        self,
        dataset_key: str,
        stock_codes: list[str],
        calendar_days: list[date],
        *,
        force: bool,
    ) -> dict[date, set[str]]:
        """一次 SQL 找出所有证券确认覆盖区间内的真实缺口。

        不能对每只证券分别执行 ``min/max + 日历反连接``：全市场修复会
        退化为上万次 SQL。这里先聚合每证券的本地覆盖边界，再与交易日历
        反连接，结果一次返回为 ``交易日 -> 证券集合``。
        """
        if not stock_codes:
            return {}
        model, partition_column, date_column, _ = self._model_columns(dataset_key)
        assert partition_column is not None and date_column is not None
        error_condition, _ = self._quality_conditions(dataset_key)
        bounds = (
            self._db.query(
                partition_column.label("stock_code"),
                func.min(date_column).label("earliest_date"),
                func.max(date_column).label("latest_date"),
            )
            .filter(partition_column.in_(stock_codes))
            .group_by(partition_column)
            .subquery()
        )
        join_condition = and_(
            partition_column == bounds.c.stock_code,
            date_column == TradingCalendarModel.trade_date,
            not_(error_condition),
        )
        rows = (
            self._db.query(bounds.c.stock_code, TradingCalendarModel.trade_date)
            .select_from(bounds)
            .join(
                TradingCalendarModel,
                and_(
                    TradingCalendarModel.is_trading_day.is_(True),
                    TradingCalendarModel.trade_date >= bounds.c.earliest_date,
                    TradingCalendarModel.trade_date <= bounds.c.latest_date,
                ),
            )
            .outerjoin(model, join_condition)
            .filter(getattr(model, "id").is_(None))
            .order_by(TradingCalendarModel.trade_date, bounds.c.stock_code)
            .all()
        )
        exemptions: dict[str, list[dict[str, Any]]] = {
            row.partition_key: list(row.upstream_missing_ranges or [])
            for row in self._db.query(DataHealthSnapshotModel)
            .filter(
                DataHealthSnapshotModel.dataset_key == dataset_key,
                DataHealthSnapshotModel.partition_key.in_(stock_codes),
            )
            .all()
        }
        exempt_dates_by_stock = {
            stock_code: self._range_dates(ranges, calendar_days)
            for stock_code, ranges in exemptions.items()
        }
        targets: dict[date, set[str]] = {}
        for stock_code, trade_date in rows:
            if force or trade_date not in exempt_dates_by_stock.get(stock_code, set()):
                targets.setdefault(trade_date, set()).add(stock_code)
        return targets

    def _daily_gap_targets_for_repair(
        self,
        dataset_key: str,
        partition_keys: list[str],
        calendar_days: list[date],
        *,
        force: bool,
    ) -> dict[str, set[date]]:
        """一次集合反连接找出各日频分区需要复验的缺口或异常日期。"""
        if not partition_keys:
            return {}
        model, partition_column, date_column, _ = self._model_columns(dataset_key)
        assert partition_column is not None and date_column is not None
        bounds = (
            self._db.query(
                partition_column.label("partition_key"),
                func.min(date_column).label("earliest_date"),
            )
            .filter(partition_column.in_(partition_keys))
            .group_by(partition_column)
            .subquery()
        )
        join_condition = and_(
            partition_column == bounds.c.partition_key,
            date_column == TradingCalendarModel.trade_date,
        )
        rows = (
            self._db.query(bounds.c.partition_key, TradingCalendarModel.trade_date)
            .select_from(bounds)
            .join(
                TradingCalendarModel,
                and_(
                    TradingCalendarModel.is_trading_day.is_(True),
                    TradingCalendarModel.trade_date >= bounds.c.earliest_date,
                    TradingCalendarModel.trade_date <= self._latest_trading_day(local_only=True),
                ),
            )
            .outerjoin(model, join_condition)
            .filter(getattr(model, "id").is_(None))
            .order_by(bounds.c.partition_key, TradingCalendarModel.trade_date)
            .all()
        )
        error_condition, _ = self._quality_conditions(dataset_key)
        is_bar_dataset = dataset_key in {"index_daily_bar", "industry_daily_bar"}
        # 指数/行业日线额外带出"仅因开高低缺失判错"标记，用于跳过已确认仅收盘价的行
        ohlc_only_flag = (
            case((self._ohlc_only_error_condition(dataset_key), True), else_=False)
            if is_bar_dataset
            else literal(False)
        )
        invalid_rows = (
            self._db.query(partition_column, date_column, ohlc_only_flag)
            .join(
                bounds,
                partition_column == bounds.c.partition_key,
            )
            .filter(
                error_condition,
                date_column >= bounds.c.earliest_date,
                date_column <= self._latest_trading_day(local_only=True),
            )
            .all()
        )
        exemptions = {
            row.partition_key: list(row.upstream_missing_ranges or [])
            for row in self._db.query(DataHealthSnapshotModel)
            .filter(
                DataHealthSnapshotModel.dataset_key == dataset_key,
                DataHealthSnapshotModel.partition_key.in_(partition_keys),
            )
            .all()
        }
        # 每个分区的豁免区间只展开一次，避免逐行对整本日历重复展开
        missing_exempt_by_key: dict[str, set[date]] = {}
        close_only_by_key: dict[str, set[date]] = {}
        for key, ranges in exemptions.items():
            close_only = [
                item
                for item in ranges
                if isinstance(item, dict) and item.get("source") == _CLOSE_ONLY_SOURCE
            ]
            missing_exempt_by_key[key] = self._range_dates(
                [item for item in ranges if item not in close_only], calendar_days
            )
            close_only_by_key[key] = self._range_dates(close_only, calendar_days)
        targets: dict[str, set[date]] = {}
        for partition_key, trade_date in rows:
            if force or trade_date not in missing_exempt_by_key.get(partition_key, set()):
                targets.setdefault(partition_key, set()).add(trade_date)
        for partition_key, trade_date, ohlc_only in invalid_rows:
            if (
                not force
                and ohlc_only
                and trade_date in close_only_by_key.get(partition_key, set())
            ):
                continue
            targets.setdefault(partition_key, set()).add(trade_date)
        return targets

    def _daily_exact_missing_summaries(
        self,
        dataset_key: str,
        partition_keys: list[str],
        calendar_days: list[date],
        upstream_missing_by_partition: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        """流式汇总分区日频真实缺口，供完整质量检查复用。

        不按分区逐一反连接，避免个股大表与多行业/指数检查退化为 N+1 查询；
        仅保留计数、前 20 个样例和仍有效的已确认上游空档。
        """
        if not partition_keys:
            return {}
        model, partition_column, date_column, _ = self._model_columns(dataset_key)
        assert partition_column is not None and date_column is not None
        bounds = (
            self._db.query(
                partition_column.label("partition_key"),
                func.min(date_column).label("earliest_date"),
                func.max(date_column).label("latest_date"),
            )
            .filter(partition_column.in_(partition_keys))
            .group_by(partition_column)
            .subquery()
        )
        join_condition = and_(
            partition_column == bounds.c.partition_key,
            date_column == TradingCalendarModel.trade_date,
        )
        known_ranges_by_partition: dict[str, list[tuple[date, date]]] = {}
        for partition_key, ranges in upstream_missing_by_partition.items():
            parsed: list[tuple[date, date]] = []
            for item in ranges:
                if not isinstance(item, dict):
                    continue
                try:
                    parsed.append(
                        (
                            date.fromisoformat(str(item["start_date"])),
                            date.fromisoformat(str(item["end_date"])),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            known_ranges_by_partition[partition_key] = parsed
        missing_count: dict[str, int] = {}
        missing_sample: dict[str, list[str]] = {}
        # (起始日、终止日、交易日数、末日的交易日日历序号)。按 SQL 排序流式
        # 压缩，避免资金流向的数百万个已豁免日期常驻内存。
        retained_groups: dict[str, list[list[Any]]] = {}
        calendar_positions = {day: index for index, day in enumerate(calendar_days)}
        coverage_end = (
            bounds.c.latest_date
            if dataset_key in _STOCK_DAILY_DATASETS
            else self._latest_trading_day(local_only=True)
        )
        rows = (
            self._db.query(bounds.c.partition_key, TradingCalendarModel.trade_date)
            .select_from(bounds)
            .join(
                TradingCalendarModel,
                and_(
                    TradingCalendarModel.is_trading_day.is_(True),
                    TradingCalendarModel.trade_date >= bounds.c.earliest_date,
                    TradingCalendarModel.trade_date <= coverage_end,
                ),
            )
            .outerjoin(model, join_condition)
            .filter(getattr(model, "id").is_(None))
            .order_by(bounds.c.partition_key, TradingCalendarModel.trade_date)
            .yield_per(10_000)
        )
        for partition_key, trade_date in rows:
            if any(
                start <= trade_date <= end
                for start, end in known_ranges_by_partition.get(partition_key, [])
            ):
                groups = retained_groups.setdefault(partition_key, [])
                position = calendar_positions[trade_date]
                if groups and position == groups[-1][3] + 1:
                    groups[-1][1] = trade_date
                    groups[-1][2] += 1
                    groups[-1][3] = position
                else:
                    groups.append([trade_date, trade_date, 1, position])
                continue
            missing_count[partition_key] = missing_count.get(partition_key, 0) + 1
            samples = missing_sample.setdefault(partition_key, [])
            if len(samples) < 20:
                samples.append(trade_date.isoformat())

        result: dict[str, dict[str, Any]] = {}
        for partition_key in partition_keys:
            existing = upstream_missing_by_partition.get(partition_key, [])
            retained_dates = {
                day
                for start, end, _, _ in retained_groups.get(partition_key, [])
                for day in calendar_days
                if start <= day <= end
            }
            result[partition_key] = {
                "missing_count": missing_count.get(partition_key, 0),
                "missing_sample": missing_sample.get(partition_key, []),
                "upstream_missing_ranges": self._remaining_upstream_missing_ranges(
                    retained_dates, calendar_days, existing
                ),
            }
        return result

    def _record_upstream_missing_dates_batch(
        self,
        dataset_key: str,
        dates_by_stock: dict[str, set[date]],
        calendar_days: list[date],
        *,
        source: str = "tushare",
    ) -> None:
        """一次加载、一次 flush 写入本轮确认的所有上游缺失区间。"""
        if not dates_by_stock:
            return
        snapshots = {
            row.partition_key: row
            for row in self._db.query(DataHealthSnapshotModel)
            .filter(
                DataHealthSnapshotModel.dataset_key == dataset_key,
                DataHealthSnapshotModel.partition_key.in_(list(dates_by_stock)),
            )
            .all()
        }
        confirmed_at = utcnow().replace(microsecond=0).isoformat() + "Z"
        for stock_code, dates in dates_by_stock.items():
            snapshot = snapshots.get(stock_code)
            if snapshot is None:
                snapshot = DataHealthSnapshotModel(
                    dataset_key=dataset_key, partition_key=stock_code
                )
                self._db.add(snapshot)
            existing = list(snapshot.upstream_missing_ranges or [])
            same_source = [
                item for item in existing if isinstance(item, dict) and item.get("source") == source
            ]
            other_sources = [
                item for item in existing if not isinstance(item, dict) or item.get("source") != source
            ]
            snapshot.upstream_missing_ranges = other_sources + self._compress_upstream_missing_ranges(
                self._range_dates(same_source, calendar_days) | dates,
                calendar_days,
                same_source,
                confirmed_at=confirmed_at,
                source=source,
            )
        self._db.flush()

    def _repair_stock_daily_gaps(
        self,
        dataset_key: str,
        partition_key: str | None,
        service: Any,
        *,
        force: bool,
    ) -> dict[str, Any]:
        """Revalidate stock gaps using Tushare's full-market response per trade date.

        Only a successful response that still leaves a requested stock/date absent
        is evidence of an upstream gap. Transport/API failures deliberately remain
        normal unresolved gaps.
        """
        stock_codes = [partition_key] if partition_key else self._partitions(dataset_key)
        calendar_days = self._calendar_days_until(
            self._latest_trading_day(local_only=True), local_calendar=True
        )
        targets_by_day = self._stock_gap_targets_for_repair(
            dataset_key, stock_codes, calendar_days, force=force
        )

        records = 0
        errors: list[str] = []
        confirmed = 0
        model, partition_column, date_column, _ = self._model_columns(dataset_key)
        assert partition_column is not None and date_column is not None
        confirmed_dates_by_stock: dict[str, set[date]] = {}
        for index, (trade_date, target_codes) in enumerate(sorted(targets_by_day.items()), start=1):
            try:
                result = service.sync_trade_date(
                    trade_date, datasets=[dataset_key], commit=False
                )
            except Exception as exc:  # upstream request did not establish a negative result
                errors.append(f"{trade_date.isoformat()}: {type(exc).__name__}: {exc}")
                continue
            records += int((result.get("records") or {}).get(dataset_key) or 0)
            request_errors = list(result.get("errors") or [])
            if request_errors:
                errors.extend(f"{trade_date.isoformat()}: {error}" for error in request_errors)
                continue
            returned_codes = {
                row[0]
                for row in self._db.query(partition_column)
                .filter(partition_column.in_(target_codes), date_column == trade_date)
                .all()
            }
            absent_codes = target_codes - returned_codes
            for stock_code in absent_codes:
                confirmed_dates_by_stock.setdefault(stock_code, set()).add(trade_date)
            confirmed += len(absent_codes)
            if index % _STOCK_REPAIR_COMMIT_BATCH_SIZE == 0:
                # 数据写入按小批提交，避免每个交易日一次事务提交的网络往返。
                self._db.commit()
        if confirmed_dates_by_stock:
            self._record_upstream_missing_dates_batch(
                dataset_key, confirmed_dates_by_stock, calendar_days
            )
        return {
            "records": records,
            "errors": errors,
            "gaps_found": sum(len(codes) for codes in targets_by_day.values()),
            "upstream_missing_confirmed": confirmed,
        }

    @staticmethod
    def _valid_repair_item(dataset_key: str, item: Any) -> bool:
        """判断上游返回行是否足以覆盖当前硬错误规则。"""
        if dataset_key in {"index_daily_bar", "industry_daily_bar"}:
            prices = ("open_price", "high_price", "low_price", "close_price")
            try:
                # 上游缺价常以 NaN 表示：NaN <= 0 为 False，必须显式按非有限值判为无效，
                # 否则"仅收盘价"的行会被当成完整行写回（入库时 NaN 清洗为 NULL）。
                if any(
                    getattr(item, name, None) is None
                    or not math.isfinite(float(getattr(item, name)))
                    or float(getattr(item, name)) <= 0
                    for name in prices
                ):
                    return False
                change = getattr(item, "change_pct", None)
                return (
                    change is None
                    or math.isnan(float(change))
                    or abs(float(change)) <= _CHANGE_PCT_ERROR_LIMIT
                )
            except (TypeError, ValueError):
                return False
        if dataset_key == "index_valuation":
            try:
                for name in ("pe_percentile", "pb_percentile"):
                    value = getattr(item, name, None)
                    if value is not None and not 0 <= float(value) <= 100:
                        return False
                for name in ("pe", "pb"):
                    value = getattr(item, name, None)
                    if value is not None and float(value) < 0:
                        return False
            except (TypeError, ValueError):
                return False
        return True

    @staticmethod
    def _close_only_repair_item(item: Any) -> bool:
        """判断上游返回行是否"只有可用收盘价"：收盘价与涨跌幅合法但开高低缺失。

        这类行可写入本地补足收盘价，并登记"上游仅收盘价"区间，
        避免同一批日期在每次修复时被反复判为硬错误。
        """
        try:
            close = getattr(item, "close_price", None)
            if close is None or not math.isfinite(float(close)) or float(close) <= 0:
                return False
            change = getattr(item, "change_pct", None)
            if change is not None and math.isfinite(float(change)) and (
                abs(float(change)) > _CHANGE_PCT_ERROR_LIMIT or float(change) <= -99.99
            ):
                return False
        except (TypeError, ValueError):
            return False
        return not DataManagementService._valid_repair_item("index_daily_bar", item)

    def _repair_index_daily_gaps(
        self, partition_key: str | None, service: Any, *, force: bool
    ) -> dict[str, Any]:
        """按真实缺口窗口复验所有指数源，只写回目标日期。

        先删除分区内落在非交易日的行（上游把假期/基日打成行情），再复验缺口。
        所有来源都只提供收盘价的日期，写入收盘价并登记为"上游仅收盘价"区间，
        不再记为上游缺失，也不再在每次检查中判为硬错误。
        """
        codes = [partition_key] if partition_key else self._partitions("index_daily_bar")
        calendar_days = self._calendar_days_until(self._latest_trading_day(local_only=True), local_calendar=True)
        non_trading_removed = self._delete_non_trading_rows("index_daily_bar", codes, calendar_days)
        # 清理单独提交，避免后续分区失败的回滚把它一起撤销
        self._db.commit()
        targets_by_code = self._daily_gap_targets_for_repair(
            "index_daily_bar", codes, calendar_days, force=force
        )
        records = confirmed = close_only_confirmed = 0
        errors: list[str] = []
        confirmed_by_code: dict[str, set[date]] = {}
        close_only_by_code: dict[str, set[date]] = {}
        for index, (code, targets) in enumerate(sorted(targets_by_code.items()), start=1):
            selected: dict[date, tuple[Any, str]] = {}
            close_only: dict[date, tuple[Any, str]] = {}
            request_failed = False
            sources = service._build_index_daily_sources()
            if not sources:
                errors.append(f"{code}: 无可用指数日线数据源")
                continue
            for start, end in self._coalesce_repair_windows(targets, calendar_days):
                for source, client in sources:
                    try:
                        bars = client.fetch_index_daily(
                            code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
                        )
                    except Exception as exc:
                        request_failed = True
                        errors.append(f"{code}/{source}: {type(exc).__name__}: {exc}")
                        continue
                    for bar in bars:
                        if bar.trade_date not in targets:
                            continue
                        if self._valid_repair_item("index_daily_bar", bar):
                            selected.setdefault(bar.trade_date, (bar, source))
                        elif self._close_only_repair_item(bar):
                            close_only.setdefault(bar.trade_date, (bar, source))
            # 只有全部来源都成功响应时，"仅收盘价"才是可信的上游结论
            close_only = (
                {}
                if request_failed
                else {day: item for day, item in close_only.items() if day not in selected}
            )
            for source in {source for _, source in [*selected.values(), *close_only.values()]}:
                bars = [
                    bar
                    for bar, row_source in [*selected.values(), *close_only.values()]
                    if row_source == source
                ]
                records += service._insert_index_bars(code, bars, source=source, overwrite=True)
            if close_only:
                close_only_by_code[code] = set(close_only)
                close_only_confirmed += len(close_only)
            absent = targets - set(selected) - set(close_only)
            if absent and not request_failed:
                confirmed_by_code[code] = absent
                confirmed += len(absent)
            if index % _DAILY_REPAIR_COMMIT_BATCH_SIZE == 0:
                self._db.commit()
        if confirmed_by_code:
            self._record_upstream_missing_dates_batch(
                "index_daily_bar", confirmed_by_code, calendar_days, source="index_multi_source"
            )
        if close_only_by_code:
            self._record_upstream_missing_dates_batch(
                "index_daily_bar", close_only_by_code, calendar_days, source=_CLOSE_ONLY_SOURCE
            )
        return {
            "records": records,
            "errors": errors,
            "gaps_found": sum(len(days) for days in targets_by_code.values()),
            "upstream_missing_confirmed": confirmed,
            "close_only_confirmed": close_only_confirmed,
            "non_trading_removed": non_trading_removed,
        }

    def _delete_non_trading_rows(
        self, dataset_key: str, partition_keys: list[str], calendar_days: list[date]
    ) -> int:
        """删除指数/行业日线中落在非交易日的行，返回删除行数。

        只处理本地日历覆盖范围内（不晚于最近交易日）的日期，避免日历尚未
        同步到的新交易日被误删。

        Args:
            dataset_key: index_daily_bar 或 industry_daily_bar。
            partition_keys: 需要清理的分区。
            calendar_days: 本地交易日列表（升序）。

        Returns:
            删除的行数。
        """
        if not partition_keys or not calendar_days:
            return 0
        model, partition_column, date_column, _ = self._model_columns(dataset_key)
        is_trading = (
            select(TradingCalendarModel.trade_date)
            .where(
                TradingCalendarModel.trade_date == date_column,
                TradingCalendarModel.is_trading_day.is_(True),
            )
            .exists()
        )
        deleted = (
            self._db.query(model)
            .filter(
                partition_column.in_(partition_keys),
                date_column <= calendar_days[-1],
                ~is_trading,
            )
            .delete(synchronize_session=False)
        )
        return deleted if isinstance(deleted, int) else 0

    @staticmethod
    def _coalesce_repair_windows(
        target_dates: set[date], calendar_days: list[date]
    ) -> list[tuple[date, date]]:
        """合并相距不超过固定交易日数的缺口，平衡请求次数与无效历史下载。"""
        positions = {day: index for index, day in enumerate(calendar_days)}
        ordered = sorted(day for day in target_dates if day in positions)
        if not ordered:
            return []
        windows: list[list[date]] = [[ordered[0], ordered[0]]]
        for day in ordered[1:]:
            previous = windows[-1][1]
            if positions[day] - positions[previous] <= _INDEX_REPAIR_WINDOW_GAP_DAYS:
                windows[-1][1] = day
            else:
                windows.append([day, day])
        return [(start, end) for start, end in windows]

    def _repair_index_valuation_gaps(
        self, partition_key: str | None, service: Any, *, force: bool
    ) -> dict[str, Any]:
        """对缺口日期独立验证 AkShare/Tushare，避免首选源掩盖可用回退数据。"""
        codes = [partition_key] if partition_key else self._partitions("index_valuation")
        calendar_days = self._calendar_days_until(self._latest_trading_day(local_only=True), local_calendar=True)
        targets_by_code = self._daily_gap_targets_for_repair(
            "index_valuation", codes, calendar_days, force=force
        )
        records = confirmed = 0
        errors: list[str] = []
        confirmed_by_code: dict[str, set[date]] = {}
        for index, (code, targets) in enumerate(sorted(targets_by_code.items()), start=1):
            # 与日常同步一致：乐咕乐股（AkShare）为主来源，Tushare 只补主来源缺失的日期
            clients: list[tuple[str, Any]] = [("akshare", AkShareIndexClient())]
            tushare = TushareIndexValuationClient()
            if tushare.is_configured() and tushare.supports(code):
                clients.append(("tushare", tushare))
            selected: dict[date, Any] = {}
            request_failed = False
            for source, client in clients:
                try:
                    values = client.fetch_index_valuation(code)
                except Exception as exc:
                    request_failed = True
                    errors.append(f"{code}/{source}: {type(exc).__name__}: {exc}")
                    continue
                for value in values:
                    if value.trade_date in targets and self._valid_repair_item("index_valuation", value):
                        selected.setdefault(value.trade_date, value)
            records += service._insert_index_valuations(code, list(selected.values()), overwrite=True)
            absent = targets - set(selected)
            if absent and not request_failed:
                confirmed_by_code[code] = absent
                confirmed += len(absent)
            if index % _DAILY_REPAIR_COMMIT_BATCH_SIZE == 0:
                self._db.commit()
        if confirmed_by_code:
            self._record_upstream_missing_dates_batch(
                "index_valuation", confirmed_by_code, calendar_days, source="valuation_multi_source"
            )
        return {
            "records": records,
            "errors": errors,
            "gaps_found": sum(len(days) for days in targets_by_code.values()),
            "upstream_missing_confirmed": confirmed,
        }

    def _repair_industry_daily_gaps(
        self, partition_key: str | None, service: Any, *, force: bool
    ) -> dict[str, Any]:
        """申万完整响应只对缺口目标写入，成功缺失则记录上游豁免。

        复验前先删除分区内落在非交易日的行，口径与指数日线一致。
        """
        codes = [partition_key] if partition_key else self._partitions("industry_daily_bar")
        calendar_days = self._calendar_days_until(self._latest_trading_day(local_only=True), local_calendar=True)
        non_trading_removed = self._delete_non_trading_rows(
            "industry_daily_bar", codes, calendar_days
        )
        # 清理单独提交，避免后续单行业失败的回滚把它一起撤销
        self._db.commit()
        targets_by_code = self._daily_gap_targets_for_repair(
            "industry_daily_bar", codes, calendar_days, force=force
        )
        records = confirmed = 0
        errors: list[str] = []
        confirmed_by_code: dict[str, set[date]] = {}
        for index, (code, targets) in enumerate(sorted(targets_by_code.items()), start=1):
            try:
                result = service.repair_industry_dates(code, targets, commit=False)
            except Exception as exc:
                self._db.rollback()
                errors.append(f"{code}: {type(exc).__name__}: {exc}")
                continue
            returned = set(result.get("returned_dates") or set())
            records += int(result.get("upserted_rows") or 0)
            absent = targets - returned
            if absent:
                confirmed_by_code[code] = absent
                confirmed += len(absent)
            if index % _DAILY_REPAIR_COMMIT_BATCH_SIZE == 0:
                self._db.commit()
        if confirmed_by_code:
            self._record_upstream_missing_dates_batch(
                "industry_daily_bar", confirmed_by_code, calendar_days, source="akshare_sw"
            )
        return {
            "records": records,
            "errors": errors,
            "gaps_found": sum(len(days) for days in targets_by_code.values()),
            "upstream_missing_confirmed": confirmed,
            "non_trading_removed": non_trading_removed,
        }

    def _model_columns(self, dataset_key: str) -> tuple[type[Any], Any, Any, Any]:
        """返回数据集对应 ORM 模型及分区、日期、来源列。"""
        mapping: dict[str, tuple[type[Any], Any, Any, Any]] = {
            "trading_calendar": (TradingCalendarModel, None, TradingCalendarModel.trade_date, None),
            "index_daily_bar": (
                IndexDailyBarModel,
                IndexDailyBarModel.index_code,
                IndexDailyBarModel.trade_date,
                IndexDailyBarModel.source,
            ),
            "index_valuation": (
                IndexValuationModel,
                IndexValuationModel.index_code,
                IndexValuationModel.trade_date,
                IndexValuationModel.source,
            ),
            "macro_indicator": (
                MacroIndicatorModel,
                MacroIndicatorModel.indicator_code,
                MacroIndicatorModel.period_date,
                MacroIndicatorModel.source,
            ),
            "index_membership": (
                IndexMemberEventModel,
                IndexMemberEventModel.index_code,
                IndexMemberEventModel.updated_at,
                IndexMemberEventModel.source,
            ),
            "industry_universe": (
                IndustryUniverseModel,
                None,
                IndustryUniverseModel.updated_at,
                None,
            ),
            "industry_daily_bar": (
                IndustryDailyBarModel,
                IndustryDailyBarModel.industry_code,
                IndustryDailyBarModel.trade_date,
                IndustryDailyBarModel.source,
            ),
            "industry_membership": (
                IndustryMembershipEventModel,
                IndustryMembershipEventModel.industry_code,
                IndustryMembershipEventModel.fetched_at,
                IndustryMembershipEventModel.source,
            ),
            "stock_universe": (
                StockUniverseModel,
                None,
                StockUniverseModel.updated_at,
                StockUniverseModel.source,
            ),
            "stock_daily_close": (
                StockDailyCloseModel,
                StockDailyCloseModel.stock_code,
                StockDailyCloseModel.trade_date,
                StockDailyCloseModel.source,
            ),
            "stock_daily_basic": (
                StockDailyBasicModel,
                StockDailyBasicModel.stock_code,
                StockDailyBasicModel.trade_date,
                StockDailyBasicModel.source,
            ),
            "stock_moneyflow": (
                StockMoneyflowModel,
                StockMoneyflowModel.stock_code,
                StockMoneyflowModel.trade_date,
                StockMoneyflowModel.source,
            ),
        }
        return mapping[dataset_key]

    def _expected_date(self, dataset_key: str, *, local_calendar: bool = False) -> date | None:
        """按数据集频率计算当前应达到的业务日期。"""
        if dataset_key in {
            "index_daily_bar",
            "index_valuation",
            "industry_daily_bar",
            "stock_daily_close",
            "stock_daily_basic",
            "stock_moneyflow",
            "trading_calendar",
        }:
            return self._latest_trading_day(local_only=local_calendar)
        if dataset_key in {"index_membership", "industry_membership"}:
            return today_cn() - timedelta(days=35)
        if dataset_key in {"industry_universe", "stock_universe"}:
            return today_cn() - timedelta(days=90)
        if dataset_key == "macro_indicator":
            return today_cn() - timedelta(days=70)
        return None

    def _latest_trading_day(self, *, local_only: bool = False) -> date:
        """获取不晚于当前业务日期的最近交易日。"""
        if local_only:
            latest = (
                self._db.query(func.max(TradingCalendarModel.trade_date))
                .filter(
                    TradingCalendarModel.is_trading_day.is_(True),
                    TradingCalendarModel.trade_date <= today_cn(),
                )
                .scalar()
            )
            if latest is None:
                raise TradingCalendarUnavailableError("本地交易日历快照为空")
            return latest
        return TradingCalendar().latest_trading_day(today_cn())

    def _quality_conditions(self, dataset_key: str) -> tuple[Any, Any]:
        """返回可由数据库执行的硬错误与告警条件。"""
        false = False
        if dataset_key in {"index_daily_bar", "industry_daily_bar"}:
            model = self._daily_bar_model(dataset_key)
            hard_error = or_(
                self._close_error_condition(model),
                self._ohlc_error_condition(model),
            )
            # 2024-09/10 科创50、创业板50 等真实单日涨幅超过 15%，A 股指数
            # 成分最大涨跌停为 30%；15%~30% 只作告警提示人工确认，不判为错误。
            warning = and_(
                func.abs(model.change_pct) > _CHANGE_PCT_WARNING_LIMIT,
                func.abs(model.change_pct) <= _CHANGE_PCT_ERROR_LIMIT,
            )
            return hard_error, warning
        if dataset_key == "index_valuation":
            return (
                or_(
                    IndexValuationModel.pe_percentile < 0,
                    IndexValuationModel.pe_percentile > 100,
                    IndexValuationModel.pb_percentile < 0,
                    IndexValuationModel.pb_percentile > 100,
                ),
                or_(IndexValuationModel.pe < 0, IndexValuationModel.pb < 0),
            )
        if dataset_key == "stock_daily_close":
            hard_error = or_(
                StockDailyCloseModel.close.is_(None),
                StockDailyCloseModel.close <= 0,
                StockDailyCloseModel.open.is_(None),
                StockDailyCloseModel.open <= 0,
                StockDailyCloseModel.high.is_(None),
                StockDailyCloseModel.high <= 0,
                StockDailyCloseModel.low.is_(None),
                StockDailyCloseModel.low <= 0,
                StockDailyCloseModel.high < StockDailyCloseModel.low,
                StockDailyCloseModel.high < StockDailyCloseModel.open,
                StockDailyCloseModel.high < StockDailyCloseModel.close,
                StockDailyCloseModel.low > StockDailyCloseModel.open,
                StockDailyCloseModel.low > StockDailyCloseModel.close,
            )
            return hard_error, false
        if dataset_key == "stock_daily_basic":
            hard_error = or_(
                StockDailyBasicModel.close.is_(None),
                StockDailyBasicModel.close <= 0,
                StockDailyBasicModel.total_mv < 0,
                StockDailyBasicModel.circ_mv < 0,
                StockDailyBasicModel.total_share < 0,
                StockDailyBasicModel.float_share < 0,
            )
            warning = and_(
                StockDailyBasicModel.pe_ttm.is_(None),
                StockDailyBasicModel.pb.is_(None),
                StockDailyBasicModel.total_mv.is_(None),
            )
            return hard_error, warning
        if dataset_key == "stock_moneyflow":
            return (
                false,
                and_(
                    StockMoneyflowModel.net_mf_vol.is_(None),
                    StockMoneyflowModel.net_mf_amount.is_(None),
                ),
            )
        if dataset_key == "macro_indicator":
            return MacroIndicatorModel.value.is_(None), false
        if dataset_key == "industry_universe":
            return or_(
                IndustryUniverseModel.industry_code == "", IndustryUniverseModel.name_cn == ""
            ), false
        if dataset_key == "stock_universe":
            return or_(StockUniverseModel.stock_code == "", StockUniverseModel.name_cn == ""), false
        if dataset_key == "index_membership":
            return or_(
                IndexMemberEventModel.stock_code == "", IndexMemberEventModel.start_date.is_(None)
            ), false
        if dataset_key == "industry_membership":
            return or_(
                IndustryMembershipEventModel.stock_code == "",
                IndustryMembershipEventModel.start_date.is_(None),
            ), false
        return false, false

    @staticmethod
    def _daily_bar_model(dataset_key: str) -> type[Any]:
        """返回指数/行业日线数据集对应的 ORM 模型。"""
        return IndexDailyBarModel if dataset_key == "index_daily_bar" else IndustryDailyBarModel

    @staticmethod
    def _close_error_condition(model: type[Any]) -> Any:
        """收盘价或涨跌幅不可用的硬错误条件（无论是否有开高低价都成立）。"""
        return or_(
            model.close_price.is_(None),
            model.close_price <= 0,
            func.abs(model.change_pct) > _CHANGE_PCT_ERROR_LIMIT,
            model.change_pct <= -99.99,
        )

    @staticmethod
    def _ohlc_error_condition(model: type[Any]) -> Any:
        """开盘/最高/最低价缺失或非正的硬错误条件。"""
        return or_(
            model.open_price.is_(None),
            model.open_price <= 0,
            model.high_price.is_(None),
            model.high_price <= 0,
            model.low_price.is_(None),
            model.low_price <= 0,
        )

    def _ohlc_only_error_condition(self, dataset_key: str) -> Any:
        """仅因开高低缺失而判错的行：收盘价与涨跌幅本身合法。

        这类行若落在"上游仅收盘价"确认区间内，可从硬错误中豁免。
        """
        model = self._daily_bar_model(dataset_key)
        return and_(self._ohlc_error_condition(model), not_(self._close_error_condition(model)))

    def _is_daily(self, dataset_key: str) -> bool:
        """判断数据集是否按交易日连续性统计缺口。"""
        return dataset_key in {
            "index_daily_bar",
            "index_valuation",
            "industry_daily_bar",
            "stock_daily_close",
            "stock_daily_basic",
            "stock_moneyflow",
        }

    def _calendar_days_until(
        self, expected: date | None, *, local_calendar: bool = False
    ) -> list[date]:
        """一次读取交易日集合，为所有分区复用日期边界。

        Raises:
            TradingCalendarUnavailableError: 交易日历不可用时抛出（严格口径 C1）。
                此处不允许退化为空列表——空列表会被解读为"没有缺口"，
                属于把"不知道"伪装成"健康"。
        """
        if expected is None:
            return []
        if local_calendar:
            days = {
                row[0]
                for row in self._db.query(TradingCalendarModel.trade_date)
                .filter(TradingCalendarModel.is_trading_day.is_(True))
                .all()
            }
            if not days:
                raise TradingCalendarUnavailableError("本地交易日历快照为空")
        else:
            days = TradingCalendar().require_trading_days()
        return sorted(day for day in days if day <= expected)

    def _missing_count_from_bounds(
        self,
        earliest: date | None,
        record_count: int,
        calendar_days: list[date],
        valid_count: int | None = None,
    ) -> int:
        """按最早记录与交易日数量计算缺口，不加载全部日期行。"""
        if earliest is None or not calendar_days:
            return 0
        expected_count = len(calendar_days) - bisect_left(calendar_days, earliest)
        present_count = record_count if valid_count is None else valid_count
        return max(0, expected_count - present_count)

    def _health_status(
        self,
        dataset_key: str,
        partition_key: str,
        record_count: int,
        latest: date | None,
        expected: date | None,
        missing: int,
        hard_errors: int,
        warnings: int,
    ) -> str:
        """按硬错误、缺口、告警和时效确定单分区状态。"""
        if (
            dataset_key == "index_valuation"
            and partition_key not in _VALUATION_SUPPORTED_CODES
            and record_count == 0
        ):
            return "unsupported"
        if record_count == 0:
            return "error"
        if hard_errors:
            return "error"
        if (
            missing
            or warnings
            or (latest is not None and expected is not None and latest < expected)
        ):
            return "warning"
        return "healthy"

    def _save_snapshot(
        self,
        definition: DataSetDefinition,
        partition_key: str,
        run_id: str,
        operation: str,
        payload: dict[str, Any],
        existing: DataHealthSnapshotModel | None = None,
        *,
        run_status: str = "success",
        last_error: str | None = None,
    ) -> DataHealthSnapshotModel:
        """写入或更新一条当前分区健康快照。

        质量指标总是按本次检查结果重算；运行状态按同步结果写入：失败或部分
        成功时把错误摘要写入 ``issue_summary.last_error``，且只有完全成功才
        刷新 ``last_success_at``，避免把失败的抓取显示成"最近一次成功"。

        Args:
            definition: 数据集定义。
            partition_key: 分区键（空串为汇总行）。
            run_id: 运行 ID。
            operation: 维护操作。
            payload: 质量检查结果字段。
            existing: 已加载的快照行。
            run_status: success/partial_success/failed。
            last_error: 失败时的错误摘要。

        Returns:
            快照 ORM 行。
        """
        row = existing
        if row is None:
            row = (
                self._db.query(DataHealthSnapshotModel)
                .filter_by(dataset_key=definition.key, partition_key=partition_key)
                .one_or_none()
            )
        if row is None:
            row = DataHealthSnapshotModel(dataset_key=definition.key, partition_key=partition_key)
            self._db.add(row)
        for key, value in payload.items():
            setattr(row, key, value)
        if last_error and run_status != "success":
            summary = dict(row.issue_summary) if isinstance(row.issue_summary, dict) else {}
            summary["last_error"] = last_error
            row.issue_summary = summary
        now = utcnow()
        row.last_run_id = run_id
        row.last_run_status = run_status
        row.last_checked_at = now
        if operation != "check":
            row.last_synced_at = now
            if run_status == "success":
                row.last_success_at = now
        return row

    def _aggregate(
        self,
        definition: DataSetDefinition,
        rows: list[DataHealthSnapshotModel],
        run_id: str,
        operation: str,
        existing: DataHealthSnapshotModel | None = None,
        *,
        run_status: str = "success",
        run_errors: list[str] | None = None,
    ) -> DataHealthSnapshotModel:
        """按分区快照生成数据集级汇总行。

        汇总行的运行状态取数据集级同步结果；分区失败数量与错误摘要写入
        ``issue_summary``，便于在数据集层面直接看到部分失败。
        """
        sources = {row.source_name for row in rows if row.source_name}
        status = "healthy"
        if any(row.health_status == "error" for row in rows):
            status = "error"
        elif any(row.health_status == "warning" for row in rows):
            status = "warning"
        elif rows and all(row.health_status == "unsupported" for row in rows):
            status = "unsupported"
        missing_count = sum(row.missing_count for row in rows)
        error_count = sum(row.invalid_count for row in rows)
        warning_count = sum(
            int((row.issue_summary or {}).get("warning_count") or 0)
            for row in rows
            if isinstance(row.issue_summary, dict)
        )
        missing_dates_sample: list[str] = []
        reasons: set[str] = set()
        for row in rows:
            summary = row.issue_summary if isinstance(row.issue_summary, dict) else {}
            missing_dates_sample.extend(
                value
                for value in summary.get("missing_dates_sample", [])
                if isinstance(value, str)
            )
            reasons.update(
                value for value in summary.get("reasons", []) if isinstance(value, str)
            )
        missing_dates_sample = sorted(set(missing_dates_sample))[:20]
        if missing_count:
            reasons.add("missing_dates")
        if error_count:
            reasons.add("invalid_values")
        if warning_count:
            reasons.add("warnings")
        issue_summary: dict[str, Any] = {
            "partition_count": len(rows),
            "error_partitions": sum(row.health_status == "error" for row in rows),
            "warning_partitions": sum(row.health_status == "warning" for row in rows),
            "failed_partitions": sum(row.last_run_status == "failed" for row in rows),
            "missing_count": missing_count,
            "missing_dates_sample": missing_dates_sample,
            "missing_dates_truncated": missing_count > len(missing_dates_sample),
            "error_count": error_count,
            "warning_count": warning_count,
            "reasons": sorted(reasons),
        }
        payload = {
            "partition_name": None,
            "health_status": status if rows else "unknown",
            "source_name": " / ".join(sorted(sources)) if sources else None,
            "earliest_date": min(
                (row.earliest_date for row in rows if row.earliest_date), default=None
            ),
            "latest_date": max((row.latest_date for row in rows if row.latest_date), default=None),
            "expected_date": max(
                (row.expected_date for row in rows if row.expected_date), default=None
            ),
            "record_count": sum(row.record_count for row in rows),
            "missing_count": missing_count,
            "invalid_count": error_count,
            # 区间豁免按证券分区保存；数据集汇总行不混合不同证券的范围。
            "upstream_missing_ranges": [],
            "issue_summary": issue_summary if rows else {"reason": "尚未检查"},
        }
        errors = list(run_errors or [])
        return self._save_snapshot(
            definition,
            "",
            run_id,
            operation,
            payload,
            existing,
            run_status=run_status,
            last_error="; ".join(errors[:5]) if errors else None,
        )

    def _mark_failure(
        self, definition: DataSetDefinition, partition_key: str | None, run_id: str, message: str
    ) -> None:
        """将维护失败写回当前快照，确保前端可见真实抓取状态。"""
        keys = [partition_key] if partition_key else [""]
        for key in keys:
            row = (
                self._db.query(DataHealthSnapshotModel)
                .filter_by(dataset_key=definition.key, partition_key=key)
                .one_or_none()
            )
            if row is None:
                row = DataHealthSnapshotModel(dataset_key=definition.key, partition_key=key)
                self._db.add(row)
            row.health_status = "error"
            row.issue_summary = {"last_error": message}
            row.last_run_id = run_id
            row.last_run_status = "failed"
            row.last_synced_at = utcnow()
        self._db.commit()

    def _summary(self, definition: DataSetDefinition) -> DataSetHealthSummary:
        """将数据集级快照转换为接口摘要；首次检查前返回 unknown。"""
        row = (
            self._db.query(DataHealthSnapshotModel)
            .filter_by(dataset_key=definition.key, partition_key="")
            .one_or_none()
        )
        values: dict[str, Any] = {}
        if row is not None:
            values = {
                key: getattr(row, key)
                for key in (
                    "health_status",
                    "earliest_date",
                    "latest_date",
                    "expected_date",
                    "record_count",
                    "missing_count",
                    "invalid_count",
                    "issue_summary",
                    "upstream_missing_ranges",
                    "source_name",
                    "last_run_id",
                    "last_run_status",
                    "last_checked_at",
                    "last_synced_at",
                    "last_success_at",
                )
            }
        return DataSetHealthSummary(
            dataset_key=definition.key,
            display_name=definition.name,
            frequency=definition.frequency,
            partition_label=definition.partition_label,
            source_label=definition.source_label,
            supported_operations=list(definition.operations),
            quality_rules=list(definition.quality_rules),
            health_status=values.pop("health_status", "unknown"),
            **values,
        )

    def _partition_schema(
        self, definition: DataSetDefinition, row: DataHealthSnapshotModel
    ) -> DataPartitionHealth:
        """将 ORM 分区快照转换为 API 模型。"""
        return DataPartitionHealth(
            **self._summary_values(definition, row),
            partition_key=row.partition_key,
            partition_name=row.partition_name,
        )

    def _summary_values(
        self, definition: DataSetDefinition, row: DataHealthSnapshotModel
    ) -> dict[str, Any]:
        """构建摘要模型共享字段。"""
        return {
            "dataset_key": definition.key,
            "display_name": definition.name,
            "frequency": definition.frequency,
            "partition_label": definition.partition_label,
            "source_label": definition.source_label,
            "source_name": row.source_name,
            "supported_operations": list(definition.operations),
            "quality_rules": list(definition.quality_rules),
            "health_status": row.health_status,
            "earliest_date": row.earliest_date,
            "latest_date": row.latest_date,
            "expected_date": row.expected_date,
            "record_count": row.record_count,
            "missing_count": row.missing_count,
            "invalid_count": row.invalid_count,
            "issue_summary": row.issue_summary,
            "upstream_missing_ranges": list(row.upstream_missing_ranges or []),
            "last_run_id": row.last_run_id,
            "last_run_status": row.last_run_status,
            "last_checked_at": row.last_checked_at,
            "last_synced_at": row.last_synced_at,
            "last_success_at": row.last_success_at,
        }

    def _definition(self, dataset_key: str | None) -> DataSetDefinition:
        """验证并返回静态数据集定义。"""
        if dataset_key is None or dataset_key not in _DATASET_BY_KEY:
            raise ValueError(f"未知数据集: {dataset_key}")
        return _DATASET_BY_KEY[dataset_key]

    def _partition_names(
        self, dataset_key: str, partition_keys: list[str]
    ) -> dict[str, str | None]:
        """批量解析可读的分区名称。

        按数据集一次查询名称字典，避免个股数据集逐分区 ``get`` 退化为上万次查询。

        Args:
            dataset_key: 数据集键。
            partition_keys: 分区键列表（空串表示汇总行）。

        Returns:
            {分区键: 名称}；汇总行为 None，查不到名称时回退为分区键本身。
        """
        name_columns: dict[str, tuple[Any, Any]] = {
            "index_daily_bar": (BenchmarkIndexModel.index_code, BenchmarkIndexModel.name_cn),
            "index_valuation": (BenchmarkIndexModel.index_code, BenchmarkIndexModel.name_cn),
            "index_membership": (BenchmarkIndexModel.index_code, BenchmarkIndexModel.name_cn),
            "industry_daily_bar": (
                IndustryUniverseModel.industry_code,
                IndustryUniverseModel.name_cn,
            ),
            "industry_membership": (
                IndustryUniverseModel.industry_code,
                IndustryUniverseModel.name_cn,
            ),
        }
        for key in _STOCK_DAILY_DATASETS:
            name_columns[key] = (StockUniverseModel.stock_code, StockUniverseModel.name_cn)
        keys = [key for key in partition_keys if key]
        names: dict[str, str | None] = {}
        if keys and dataset_key in name_columns:
            code_column, name_column = name_columns[dataset_key]
            names = {
                str(code): name
                for code, name in self._db.query(code_column, name_column)
                .filter(code_column.in_(keys))
                .all()
            }
        return {key: (names.get(key) or key) if key else None for key in partition_keys}
