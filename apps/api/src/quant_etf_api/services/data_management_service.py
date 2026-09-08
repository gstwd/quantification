"""统一数据管理服务。

本模块以静态数据集清单把不同外部数据的质量快照、维护操作和运行明细
收敛到同一入口；数据抓取仍由既有领域服务负责，避免重复实现客户端逻辑。
"""

from __future__ import annotations

import logging
import threading
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Date, case, cast, func, or_
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.base import utcnow
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
from quant_etf_api.infra.db.models.stock import StockUniverseModel
from quant_etf_api.infra.time import today_cn
from quant_etf_api.infra.trading_calendar import TradingCalendar
from quant_etf_api.schemas.data_management import (
    DataManagementOverview,
    DataPartitionHealth,
    DataSetDetailResponse,
    DataSetHealthSummary,
)
from quant_etf_api.services.run_service import RunService

logger = logging.getLogger(__name__)

# 数据管理操作可能跨多个既有服务；同一进程内串行化可避免与旧入口重复写入。
# 不同作用域的任务入队后各自到达 worker，用阻塞锁排队而不是直接失败，
# 避免"全局同步进行中用户点单数据集检查"被当作运行失败。
_operation_lock = threading.Lock()
# 低频数据集（成分/目录）在同步时按此天数判断是否需要再次拉取上游
_LOW_FREQ_REFRESH_DAYS = 7
_VALUATION_SUPPORTED_CODES = {
    "000016", "000300", "000905",
}


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
    DataSetDefinition("trading_calendar", "A 股交易日历", "日历", "Tushare / AkShare 兜底", None,
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("日期连续性", "最近交易日可用性")),
    DataSetDefinition("index_daily_bar", "指数日线行情", "日频", "Tushare 优先 · 多源兜底", "指数",
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("交易日连续性", "OHLC 与收盘价合法性", "最近交易日覆盖")),
    DataSetDefinition("index_valuation", "指数估值", "日频", "Tushare / AkShare 兜底", "指数",
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("估值覆盖率", "PE/PB 与百分位合法性", "最近可用日期")),
    DataSetDefinition("macro_indicator", "宏观指标", "月频/事件", "Tushare / AkShare 兜底", "指标",
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("发布周期新鲜度", "数值字段完整性")),
    DataSetDefinition("index_membership", "指数成分", "月频/快照", "Tushare / AkShare / Baostock", "指数",
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("当前快照新鲜度", "成员代码与生效日期完整性")),
    DataSetDefinition("industry_universe", "申万行业基础信息", "低频", "申万官网", None,
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("目录数量", "行业代码与名称完整性")),
    DataSetDefinition("industry_daily_bar", "申万行业日线", "日频", "申万官网 / AkShare", "行业",
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("交易日连续性", "OHLC 与涨跌幅完整性", "最近交易日覆盖")),
    DataSetDefinition("industry_membership", "申万行业成分", "低频", "申万官网", "行业",
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("刷新期限", "股票、行业与生效日期完整性")),
    DataSetDefinition("stock_universe", "证券基础信息", "低频", "Tushare / 交易所 / AkShare", None,
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("代码、名称与状态完整性")),
    DataSetDefinition("stock_daily_close", "个股日线收盘", "日频", "Tushare / Baostock / AkShare", "股票",
                      ("sync_latest", "check", "repair_gaps", "rebuild"),
                      ("交易日连续性", "收盘价合法性", "最近交易日覆盖")),
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

        Returns:
            数据管理页所需的摘要与状态计数。
        """
        from quant_etf_api.config.settings import get_settings

        settings = get_settings()
        items = [self._summary(definition) for definition in DATASETS]
        return DataManagementOverview(
            schedule_time=(
                f"{settings.schedule_time} 自动全局同步"
                f" · {settings.industry_refresh_time} 增量补拉（北京时间）"
            ),
            datasets=items,
            healthy_count=sum(item.health_status == "healthy" for item in items),
            warning_count=sum(item.health_status == "warning" for item in items),
            error_count=sum(item.health_status == "error" for item in items),
            unknown_count=sum(item.health_status == "unknown" for item in items),
        )

    def detail(
        self,
        dataset_key: str,
        offset: int,
        limit: int,
        partition_key: str | None = None,
    ) -> DataSetDetailResponse:
        """返回指定数据集的规则与分区健康快照。

        Args:
            dataset_key: 静态数据集键。
            offset: 分区分页偏移量。
            limit: 分区分页大小。
            partition_key: 可选分区键，指定时仅返回该分区。

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
        total = query.count()
        rows = query.order_by(DataHealthSnapshotModel.partition_key).offset(offset).limit(limit).all()
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
        with _operation_lock:
            definitions = [self._definition(dataset_key)] if dataset_key else list(DATASETS)
            result_items: list[dict[str, Any]] = []
            run_service = RunService(self._db)
            for definition in definitions:
                try:
                    metrics = self._execute_dataset(
                        definition, operation, partition_key, run_id, force
                    )
                    status = _dataset_status(metrics)
                    result_items.append({"dataset_key": definition.key, "status": status, **metrics})
                    run_service.add_item(run_id, definition.key, status, metrics=metrics)
                except Exception as exc:
                    self._db.rollback()
                    message = f"{type(exc).__name__}: {exc}"
                    self._mark_failure(definition, partition_key, run_id, message)
                    result_items.append({"dataset_key": definition.key, "status": "failed", "error": message})
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
                    else "failed"
                    if success_count == 0
                    else "partial_success"
                ),
            }

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
        sync_result: dict[str, Any] = {"records": 0, "errors": [], "skipped": False}
        if operation != "check":
            sync_result = self._sync(definition.key, partition_key, operation, force)
        snapshots = self._check(definition, partition_key, run_id, operation)
        self._db.commit()
        errors = list(sync_result.get("errors") or [])
        return {
            "checked_partitions": len(snapshots),
            "operation": operation,
            "records": int(sync_result.get("records") or 0),
            "error_count": len(errors),
            "errors": errors[:20],
            "skipped": bool(sync_result.get("skipped")),
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

        def _fail(scope: str, exc: Exception) -> None:
            """回滚当前会话并记录分区级失败。"""
            self._db.rollback()
            errors.append(f"{scope}: {type(exc).__name__}: {exc}")

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
                new_days = sorted(trading_days - existing_dates)
                for trade_date in new_days:
                    self._db.add(TradingCalendarModel(trade_date=trade_date, is_trading_day=True))
                records = len(new_days)
                self._db.commit()
            except Exception as exc:  # noqa: PERF203
                _fail("trading_calendar", exc)
        elif dataset_key == "index_daily_bar":
            service = IngestService(self._db)
            for code in self._partitions(dataset_key) if not partition_key else [partition_key]:
                try:
                    if operation == "rebuild":
                        records += self._safe_replace_index_bars(service, code)
                        continue
                    latest = (
                        self._db.query(func.max(IndexDailyBarModel.trade_date))
                        .filter(IndexDailyBarModel.index_code == code)
                        .scalar()
                    )
                    if latest is not None and latest >= expected and not force and operation == "sync_latest":
                        skipped = True
                        continue
                    records += service._fetch_and_upsert_index_bars(
                        code, incremental=operation != "repair_gaps"
                    )
                except Exception as exc:  # noqa: PERF203
                    _fail(code, exc)
        elif dataset_key == "index_valuation":
            service = IngestService(self._db)
            for code in self._partitions(dataset_key) if not partition_key else [partition_key]:
                try:
                    if operation == "rebuild":
                        records += self._safe_replace_index_valuations(service, code)
                        continue
                    latest = (
                        self._db.query(func.max(IndexValuationModel.trade_date))
                        .filter(IndexValuationModel.index_code == code)
                        .scalar()
                    )
                    if latest is not None and latest >= expected and not force and operation == "sync_latest":
                        skipped = True
                        continue
                    records += service._fetch_and_upsert_index_valuation(code)
                except Exception as exc:  # noqa: PERF203
                    _fail(code, exc)
        elif dataset_key == "macro_indicator":
            try:
                records += IngestService(self._db)._fetch_and_upsert_macro()
            except Exception as exc:  # noqa: PERF203
                _fail("macro_indicator", exc)
        elif dataset_key == "index_membership":
            codes = self._partitions(dataset_key) if not partition_key else [partition_key]
            if codes and not force:
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
                    latest_by.get(code) is not None
                    and latest_by[code].date() >= cutoff
                    for code in codes
                ):
                    skipped = True
            if not skipped:
                try:
                    result = IndexMembershipDataService(self._db).refresh_current_snapshots(codes)
                    records += sum(int(v) for v in result.get("items", {}).values())
                    errors.extend(str(e) for e in result.get("errors", []))
                except Exception as exc:  # noqa: PERF203
                    _fail("index_membership", exc)
        elif dataset_key == "industry_universe":
            try:
                result = IndustryDataService(self._db).sync_universe()
                records += int(result.get("added") or 0) + int(result.get("updated") or 0)
            except Exception as exc:  # noqa: PERF203
                _fail("industry_universe", exc)
        elif dataset_key == "industry_daily_bar":
            service = IndustryDataService(self._db)
            if partition_key:
                try:
                    if operation == "rebuild":
                        result = service.rebuild_industry(partition_key)
                        records += int(result.get("upserted_rows") or 0)
                    elif force or self._industry_code_behind(partition_key, expected):
                        result = service.fill_industry(partition_key)
                        records += int(result.get("fetched_rows") or 0)
                    else:
                        skipped = True
                except Exception as exc:  # noqa: PERF203
                    _fail(partition_key, exc)
            elif force or self._industry_dataset_behind(expected):
                try:
                    result = service.refresh_industry_bars_incremental()
                    records += int(result.get("records") or 0)
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
                records += int(result.get("total") or 0)
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
                    records += int(result.get("added") or 0) + int(result.get("updated") or 0)
                except Exception as exc:  # noqa: PERF203
                    _fail("stock_universe", exc)
        elif dataset_key == "stock_daily_close":
            if partition_key:
                service = StockDataService(self._db)
                try:
                    if operation == "rebuild":
                        result = service.rebuild_stock(partition_key)
                        records += int(result.get("upserted_rows") or 0)
                    else:
                        result = service.fill_stock(partition_key)
                        records += int(result.get("fetched_rows") or 0)
                except Exception as exc:  # noqa: PERF203
                    _fail(partition_key, exc)
            else:
                # 全局日频同步只补当天快照，不在调度器中重拉全部历史个股。
                try:
                    records += IndustryDataService(self._db).refresh_stock_close_snapshot(expected)
                except Exception as exc:  # noqa: PERF203
                    _fail("stock_daily_close", exc)
        else:
            raise ValueError(f"未实现数据集同步: {dataset_key}")
        return {"records": records, "errors": errors, "skipped": skipped}

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
        return any(
            latest_by.get(code) is None or latest_by[code] < expected
            for code in codes
        )

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
        """安全全量重拉单指数日线：先校验覆盖范围再删除写回。"""
        bars, source = service._fetch_index_daily_multi_source(code)
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
        self._db.query(IndexDailyBarModel).filter(
            IndexDailyBarModel.index_code == code
        ).delete(synchronize_session=False)
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
        self._db.query(IndexValuationModel).filter(
            IndexValuationModel.index_code == code
        ).delete(synchronize_session=False)
        service._insert_index_valuations(code, values)
        self._db.commit()
        return len(values)

    def _check(
        self,
        definition: DataSetDefinition,
        partition_key: str | None,
        run_id: str,
        operation: str,
    ) -> list[DataHealthSnapshotModel]:
        """重算指定数据集范围的健康快照并持久化。

        无分区数据集（partition_label is None）的检查结果本身就是汇总行，
        直接以 "" 分区保存，不再二次聚合覆盖；只有分区数据集在整集检查时
        才重算汇总行，单分区检查只更新对应分区。
        """
        partitions = [partition_key] if partition_key else self._partitions(definition.key)
        payloads = self._inspect_many(definition, partitions)
        existing_rows = (
            self._db.query(DataHealthSnapshotModel)
            .filter(DataHealthSnapshotModel.dataset_key == definition.key)
            .all()
        )
        existing = {row.partition_key: row for row in existing_rows}
        result: list[DataHealthSnapshotModel] = []
        for key, payload in payloads.items():
            snapshot = self._save_snapshot(
                definition, key, run_id, operation, payload, existing.get(key)
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
        self._aggregate(definition, aggregate_rows, run_id, operation, existing.get(""))
        return result

    def _partitions(self, dataset_key: str) -> list[str]:
        """返回数据集当前可维护分区列表。"""
        if dataset_key in {"index_daily_bar", "index_valuation", "index_membership"}:
            return [r[0] for r in self._db.query(BenchmarkIndexModel.index_code).filter(BenchmarkIndexModel.is_active.is_(True)).all()]
        if dataset_key == "macro_indicator":
            return [r[0] for r in self._db.query(MacroIndicatorModel.indicator_code).distinct().all()] or ["cpi", "pmi", "lpr1y", "lpr5y"]
        if dataset_key == "industry_daily_bar":
            return [r[0] for r in self._db.query(IndustryUniverseModel.industry_code).filter(IndustryUniverseModel.is_active.is_(True)).all()]
        if dataset_key == "industry_membership":
            return [r[0] for r in self._db.query(IndustryUniverseModel.industry_code).all()]
        if dataset_key == "stock_daily_close":
            return [r[0] for r in self._db.query(StockUniverseModel.stock_code).filter(StockUniverseModel.is_active.is_(True)).all()]
        return []

    def _inspect_many(
        self,
        definition: DataSetDefinition,
        partitions: list[str],
    ) -> dict[str, dict[str, Any]]:
        """用聚合 SQL 一次计算一个数据集的所有分区健康指标。

        不把历史行情 ORM 实体加载到 Python。个股日线等大表只扫描一次，
        由数据库完成行数、日期边界和字段异常计数，避免 N+1 查询与长时间
        占用应用进程内存。
        """
        model, partition_column, date_column, source_column = self._model_columns(definition.key)
        date_expression = cast(date_column, Date) if date_column is not None else None
        error_condition, warning_condition = self._quality_conditions(definition.key)
        columns: list[Any] = [
            func.count().label("record_count"),
            func.coalesce(func.sum(case((error_condition, 1), else_=0)), 0).label("error_count"),
            func.coalesce(func.sum(case((warning_condition, 1), else_=0)), 0).label("warning_count"),
        ]
        if date_expression is not None:
            columns.extend([
                func.min(date_expression).label("earliest_date"),
                func.max(date_expression).label("latest_date"),
            ])
        if source_column is not None:
            columns.append(func.max(source_column).label("source_name"))

        if definition.partition_label is None:
            row = self._db.query(*columns).select_from(model).one()
            raw_rows = {"": row}
        else:
            query = self._db.query(partition_column.label("partition_key"), *columns).select_from(model)
            if partitions:
                query = query.filter(partition_column.in_(partitions))
            raw_rows = {str(row.partition_key): row for row in query.group_by(partition_column).all()}

        keys = [""] if definition.partition_label is None else partitions
        expected = self._expected_date(definition.key)
        calendar_days = self._calendar_days_until(expected) if self._is_daily(definition.key) else []
        result: dict[str, dict[str, Any]] = {}
        for key in keys:
            row = raw_rows.get(key)
            record_count = int(row.record_count) if row is not None else 0
            earliest = row.earliest_date if row is not None and date_expression is not None else None
            latest = row.latest_date if row is not None and date_expression is not None else None
            hard_errors = int(row.error_count) if row is not None else 0
            warnings = int(row.warning_count) if row is not None else 0
            missing = self._missing_count_from_bounds(earliest, record_count, calendar_days)
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
            if warnings:
                reasons.append("warnings")
            issues = {
                "missing_count": missing,
                "error_count": hard_errors,
                "warning_count": warnings,
                "reasons": reasons,
            }
            result[key] = {
                "partition_name": self._partition_name(definition.key, key),
                "health_status": status,
                "source_name": row.source_name if row is not None and source_column is not None else None,
                "earliest_date": earliest,
                "latest_date": latest,
                "expected_date": expected,
                "record_count": record_count,
                "missing_count": missing,
                "invalid_count": hard_errors,
                "issue_summary": issues if any(issues.values()) or status in {"error", "warning", "unknown"} else None,
            }
        return result

    def _model_columns(self, dataset_key: str) -> tuple[type[Any], Any, Any, Any]:
        """返回数据集对应 ORM 模型及分区、日期、来源列。"""
        mapping: dict[str, tuple[type[Any], Any, Any, Any]] = {
            "trading_calendar": (TradingCalendarModel, None, TradingCalendarModel.trade_date, None),
            "index_daily_bar": (IndexDailyBarModel, IndexDailyBarModel.index_code, IndexDailyBarModel.trade_date, IndexDailyBarModel.source),
            "index_valuation": (IndexValuationModel, IndexValuationModel.index_code, IndexValuationModel.trade_date, IndexValuationModel.source),
            "macro_indicator": (MacroIndicatorModel, MacroIndicatorModel.indicator_code, MacroIndicatorModel.period_date, MacroIndicatorModel.source),
            "index_membership": (IndexMemberEventModel, IndexMemberEventModel.index_code, IndexMemberEventModel.updated_at, IndexMemberEventModel.source),
            "industry_universe": (IndustryUniverseModel, None, IndustryUniverseModel.updated_at, None),
            "industry_daily_bar": (IndustryDailyBarModel, IndustryDailyBarModel.industry_code, IndustryDailyBarModel.trade_date, IndustryDailyBarModel.source),
            "industry_membership": (IndustryMembershipEventModel, IndustryMembershipEventModel.industry_code, IndustryMembershipEventModel.fetched_at, IndustryMembershipEventModel.source),
            "stock_universe": (StockUniverseModel, None, StockUniverseModel.updated_at, StockUniverseModel.source),
            "stock_daily_close": (StockDailyCloseModel, StockDailyCloseModel.stock_code, StockDailyCloseModel.trade_date, StockDailyCloseModel.source),
        }
        return mapping[dataset_key]

    def _expected_date(self, dataset_key: str) -> date | None:
        """按数据集频率计算当前应达到的业务日期。"""
        if dataset_key in {"index_daily_bar", "index_valuation", "industry_daily_bar", "stock_daily_close", "trading_calendar"}:
            return self._latest_trading_day()
        if dataset_key in {"index_membership", "industry_membership"}:
            return today_cn() - timedelta(days=35)
        if dataset_key in {"industry_universe", "stock_universe"}:
            return today_cn() - timedelta(days=90)
        if dataset_key == "macro_indicator":
            return today_cn() - timedelta(days=70)
        return None

    def _latest_trading_day(self) -> date:
        """获取不晚于当前业务日期的最近交易日。"""
        return TradingCalendar().latest_trading_day(today_cn())

    def _quality_conditions(self, dataset_key: str) -> tuple[Any, Any]:
        """返回可由数据库执行的硬错误与告警条件。"""
        false = False
        if dataset_key in {"index_daily_bar", "industry_daily_bar"}:
            model = IndexDailyBarModel if dataset_key == "index_daily_bar" else IndustryDailyBarModel
            hard_error = or_(
                func.abs(model.change_pct) > 15,
                model.close_price.is_(None), model.close_price <= 0,
                model.open_price.is_(None), model.open_price <= 0,
                model.high_price.is_(None), model.high_price <= 0,
                model.low_price.is_(None), model.low_price <= 0,
            )
            warning = (model.volume == 0) & (func.abs(model.change_pct) > 0.01)
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
            return or_(StockDailyCloseModel.close.is_(None), StockDailyCloseModel.close <= 0), false
        if dataset_key == "macro_indicator":
            return MacroIndicatorModel.value.is_(None), false
        if dataset_key == "industry_universe":
            return or_(IndustryUniverseModel.industry_code == "", IndustryUniverseModel.name_cn == ""), false
        if dataset_key == "stock_universe":
            return or_(StockUniverseModel.stock_code == "", StockUniverseModel.name_cn == ""), false
        if dataset_key == "index_membership":
            return or_(IndexMemberEventModel.stock_code == "", IndexMemberEventModel.start_date.is_(None)), false
        if dataset_key == "industry_membership":
            return or_(IndustryMembershipEventModel.stock_code == "", IndustryMembershipEventModel.start_date.is_(None)), false
        return false, false

    def _is_daily(self, dataset_key: str) -> bool:
        """判断数据集是否按交易日连续性统计缺口。"""
        return dataset_key in {"index_daily_bar", "index_valuation", "industry_daily_bar", "stock_daily_close"}

    def _calendar_days_until(self, expected: date | None) -> list[date]:
        """一次读取交易日集合，为所有分区复用日期边界。"""
        if expected is None:
            return []
        days = TradingCalendar().get_trading_days_set()
        if days is None:
            return []
        return sorted(day for day in days if day <= expected)

    def _missing_count_from_bounds(
        self,
        earliest: date | None,
        record_count: int,
        calendar_days: list[date],
    ) -> int:
        """按最早记录与交易日数量计算缺口，不加载全部日期行。"""
        if earliest is None or not calendar_days:
            return 0
        expected_count = len(calendar_days) - bisect_left(calendar_days, earliest)
        return max(0, expected_count - record_count)

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
        if dataset_key == "index_valuation" and partition_key not in _VALUATION_SUPPORTED_CODES and record_count == 0:
            return "unsupported"
        if record_count == 0:
            return "error"
        if hard_errors:
            return "error"
        if missing or warnings or (latest is not None and expected is not None and latest < expected):
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
    ) -> DataHealthSnapshotModel:
        """写入或更新一条当前分区健康快照。"""
        row = existing
        if row is None:
            row = self._db.query(DataHealthSnapshotModel).filter_by(
                dataset_key=definition.key, partition_key=partition_key
            ).one_or_none()
        if row is None:
            row = DataHealthSnapshotModel(dataset_key=definition.key, partition_key=partition_key)
            self._db.add(row)
        for key, value in payload.items():
            setattr(row, key, value)
        now = utcnow()
        row.last_run_id = run_id
        row.last_run_status = "success"
        row.last_checked_at = now
        if operation != "check":
            row.last_synced_at = now
            row.last_success_at = now
        return row

    def _aggregate(
        self,
        definition: DataSetDefinition,
        rows: list[DataHealthSnapshotModel],
        run_id: str,
        operation: str,
        existing: DataHealthSnapshotModel | None = None,
    ) -> DataHealthSnapshotModel:
        """按分区快照生成数据集级汇总行。"""
        sources = {row.source_name for row in rows if row.source_name}
        status = "healthy"
        if any(row.health_status == "error" for row in rows):
            status = "error"
        elif any(row.health_status == "warning" for row in rows):
            status = "warning"
        elif rows and all(row.health_status == "unsupported" for row in rows):
            status = "unsupported"
        payload = {
            "partition_name": None,
            "health_status": status if rows else "unknown",
            "source_name": " / ".join(sorted(sources)) if sources else None,
            "earliest_date": min((row.earliest_date for row in rows if row.earliest_date), default=None),
            "latest_date": max((row.latest_date for row in rows if row.latest_date), default=None),
            "expected_date": max((row.expected_date for row in rows if row.expected_date), default=None),
            "record_count": sum(row.record_count for row in rows),
            "missing_count": sum(row.missing_count for row in rows),
            "invalid_count": sum(row.invalid_count for row in rows),
            "issue_summary": {
                "partition_count": len(rows),
                "error_partitions": sum(row.health_status == "error" for row in rows),
                "warning_partitions": sum(row.health_status == "warning" for row in rows),
            } if rows else {"reason": "尚未检查"},
        }
        return self._save_snapshot(definition, "", run_id, operation, payload, existing)

    def _mark_failure(self, definition: DataSetDefinition, partition_key: str | None, run_id: str, message: str) -> None:
        """将维护失败写回当前快照，确保前端可见真实抓取状态。"""
        keys = [partition_key] if partition_key else [""]
        for key in keys:
            row = self._db.query(DataHealthSnapshotModel).filter_by(dataset_key=definition.key, partition_key=key).one_or_none()
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
        row = self._db.query(DataHealthSnapshotModel).filter_by(dataset_key=definition.key, partition_key="").one_or_none()
        values: dict[str, Any] = {}
        if row is not None:
            values = {key: getattr(row, key) for key in (
                "health_status", "earliest_date", "latest_date", "expected_date", "record_count",
                "missing_count", "invalid_count", "issue_summary", "source_name", "last_run_id", "last_run_status",
                "last_checked_at", "last_synced_at", "last_success_at",
            )}
        return DataSetHealthSummary(
            dataset_key=definition.key,
            display_name=definition.name,
            frequency=definition.frequency,
            partition_label=definition.partition_label,
            source_label=definition.source_label,
            supported_operations=list(definition.operations),
            health_status=values.pop("health_status", "unknown"),
            **values,
        )

    def _partition_schema(self, definition: DataSetDefinition, row: DataHealthSnapshotModel) -> DataPartitionHealth:
        """将 ORM 分区快照转换为 API 模型。"""
        return DataPartitionHealth(
            **self._summary_values(definition, row),
            partition_key=row.partition_key,
            partition_name=row.partition_name,
        )

    def _summary_values(self, definition: DataSetDefinition, row: DataHealthSnapshotModel) -> dict[str, Any]:
        """构建摘要模型共享字段。"""
        return {
            "dataset_key": definition.key, "display_name": definition.name, "frequency": definition.frequency,
            "partition_label": definition.partition_label, "source_label": definition.source_label,
            "source_name": row.source_name,
            "supported_operations": list(definition.operations), "health_status": row.health_status,
            "earliest_date": row.earliest_date, "latest_date": row.latest_date, "expected_date": row.expected_date,
            "record_count": row.record_count, "missing_count": row.missing_count, "invalid_count": row.invalid_count,
            "issue_summary": row.issue_summary, "last_run_id": row.last_run_id, "last_run_status": row.last_run_status,
            "last_checked_at": row.last_checked_at, "last_synced_at": row.last_synced_at,
            "last_success_at": row.last_success_at,
        }

    def _definition(self, dataset_key: str | None) -> DataSetDefinition:
        """验证并返回静态数据集定义。"""
        if dataset_key is None or dataset_key not in _DATASET_BY_KEY:
            raise ValueError(f"未知数据集: {dataset_key}")
        return _DATASET_BY_KEY[dataset_key]

    def _partition_name(self, dataset_key: str, partition_key: str) -> str | None:
        """解析可读的分区名称。"""
        if not partition_key:
            return None
        if dataset_key in {"index_daily_bar", "index_valuation", "index_membership"}:
            row = self._db.get(BenchmarkIndexModel, partition_key)
            return row.name_cn if row else partition_key
        if dataset_key in {"industry_daily_bar", "industry_membership"}:
            row = self._db.get(IndustryUniverseModel, partition_key)
            return row.name_cn if row else partition_key
        if dataset_key == "stock_daily_close":
            row = self._db.get(StockUniverseModel, partition_key)
            return row.name_cn if row else partition_key
        return partition_key
