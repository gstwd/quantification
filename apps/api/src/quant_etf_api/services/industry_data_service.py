"""申万行业轮动数据服务：目录同步、日线/成分/个股收盘摄取。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.industry.bars import derive_prev_close_change
from quant_etf_api.domain.industry.constants import (
    SW_CLASSIFICATION_EPOCH,
    SW_CLASSIFICATION_PREFIX_TO_L1,
    SW_CLASSIFICATION_REMAP_SINCE,
    SW_EXCLUDED_INDUSTRY_CODES,
    SW_L1_NAMES,
    normalize_sw_code,
)
from quant_etf_api.domain.industry.quality import compute_industry_bar_quality
from quant_etf_api.infra.clients.sw_industry_client import SwIndustryClient
from quant_etf_api.infra.clients.stock_close_client import StockCloseClient
from quant_etf_api.infra.clients.tushare_market import TushareStockClient
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.industry import IndustryUniverseModel
from quant_etf_api.infra.db.models.stock import StockUniverseModel
from quant_etf_api.infra.db.repositories.industry import (
    IndustryDailyBarRepository,
    IndustryMembershipEventRepository,
    IndustryUniverseRepository,
    StockDailyCloseRepository,
)
from quant_etf_api.infra.trading_calendar import TradingCalendar
from quant_etf_api.infra.time import today_cn
from quant_etf_api.schemas.industry import IndustryQualityDetail, IndustrySummaryItem

logger = logging.getLogger(__name__)

_MEMBERSHIP_REFRESH_DAYS = 7
_INCREMENTAL_BUFFER_DAYS = 45
_CLOSE_BACKFILL_DEFAULT_START = "20130101"


class IndustryDataService:
    """申万行业数据摄取与目录同步服务。"""

    def __init__(self, db: Session) -> None:
        """初始化行业数据服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._universe_repo = IndustryUniverseRepository(db)
        self._bar_repo = IndustryDailyBarRepository(db)
        self._membership_repo = IndustryMembershipEventRepository(db)
        self._close_repo = StockDailyCloseRepository(db)
        self._sw_client = SwIndustryClient()
        self._close_client = StockCloseClient()
        self._tushare_stock = TushareStockClient()
        self._calendar = TradingCalendar()
        self._trading_cache: tuple[date, date, list[date]] | None = None

    # ------------------------------------------------------------------
    # 行业目录
    # ------------------------------------------------------------------

    def sync_universe(self) -> dict[str, int]:
        """同步申万一级行业目录常量到 industry_universe。

        Returns:
            新增/更新/停用数量统计。
        """
        added = 0
        updated = 0
        for code, name in SW_L1_NAMES.items():
            row = (
                self._db.query(IndustryUniverseModel)
                .filter(IndustryUniverseModel.industry_code == code)
                .first()
            )
            if row is None:
                self._db.add(
                    IndustryUniverseModel(
                        industry_code=code,
                        name_cn=name,
                        classification="sw",
                        is_benchmark_excluded=code in SW_EXCLUDED_INDUSTRY_CODES,
                        is_active=True,
                    )
                )
                added += 1
                continue
            row.is_active = True
            if row.name_cn != name:
                row.name_cn = name
                updated += 1
            if row.is_benchmark_excluded != (code in SW_EXCLUDED_INDUSTRY_CODES):
                row.is_benchmark_excluded = code in SW_EXCLUDED_INDUSTRY_CODES
                updated += 1
        self._db.commit()
        logger.info("行业目录同步完成: added=%s updated=%s", added, updated)
        return {"added": added, "updated": updated}

    # ------------------------------------------------------------------
    # 行业指数日线
    # ------------------------------------------------------------------

    def backfill_industry_bars(self, industry_codes: list[str] | None = None) -> dict[str, Any]:
        """全量回填行业指数日线（从申万官网全历史，顺带补写派生列）。

        每个行业独立提交事务；某个行业失败时先回滚再继续处理后续行业，
        避免“当前事务已中止”或超长事务导致的连接层错误拖垮整批回填。
        """
        codes = self._resolve_codes(industry_codes)
        total = 0
        errors: list[str] = []
        for code in codes:
            try:
                rows = self._sw_client.fetch_daily(code)
                total += self._upsert_bars(code, rows)
                self._db.commit()
            except Exception as e:
                self._db.rollback()
                logger.warning("行业 %s 日线全量回填失败: %s", code, e)
                errors.append(f"{code}: {type(e).__name__}: {e}")
        return {"codes": len(codes), "records": total, "errors": errors}

    def refresh_industry_bars_incremental(
        self, industry_codes: list[str] | None = None
    ) -> dict[str, Any]:
        """增量更新行业指数日线（从库内最新日期回带缓冲窗口补到最新）。

        申万上游接口不支持按日期增量下载（P09），当前仍为全量下载后本地
        裁剪；以拉取窗口前最近收盘作为 prev_close_override，保证窗口首行
        的 prev_close/change_pct 派生正确。逐行业失败进入 errors 返回给
        调用方（不再仅记日志）。

        Returns:
            {codes, records, errors}；errors 为空表示全部成功。
            每个行业独立提交，某个行业失败不会影响后续行业。
        """
        codes = self._resolve_codes(industry_codes)
        total = 0
        errors: list[str] = []
        for code in codes:
            try:
                latest = self._bar_repo.latest_trade_date(code)
                if latest is None:
                    rows = self._sw_client.fetch_daily(code)
                    total += self._upsert_bars(code, rows)
                else:
                    start = latest - timedelta(days=_INCREMENTAL_BUFFER_DAYS)
                    rows = self._sw_client.fetch_daily(
                        code, start_date=start.strftime("%Y%m%d")
                    )
                    prev_close = self._bar_repo.find_close_before(code, start)
                    total += self._upsert_bars(
                        code, rows, prev_close_override=prev_close
                    )
                self._db.commit()
            except Exception as e:
                self._db.rollback()
                errors.append(f"{code}: {type(e).__name__}: {e}")
                logger.warning("行业 %s 日线增量更新失败: %s", code, e)
        return {"codes": len(codes), "records": total, "errors": errors}

    def _upsert_bars(
        self,
        code: str,
        rows: list[dict[str, Any]],
        prev_close_override: float | None = None,
    ) -> int:
        """将行业日线行按升序补写派生列后幂等写入 industry_daily_bar。

        Args:
            code: 申万一级行业代码。
            rows: 拉取的日线字典列表（可为乱序，按 trade_date 排序后处理）。
            prev_close_override: 拉取窗口之前库内最近收盘价，None 表示无前收。

        Returns:
            参与写入的行数。
        """
        if not rows:
            return 0
        ordered = sorted(
            (row for row in rows if row.get("close_price") is not None),
            key=lambda row: row["trade_date"],
        )
        if not ordered:
            return 0
        derived = derive_prev_close_change(ordered, prev_close_override=prev_close_override)
        values = []
        for row in derived:
            values.append(
                {
                    "trade_date": row["trade_date"],
                    "industry_code": code,
                    "open_price": row.get("open_price"),
                    "high_price": row.get("high_price"),
                    "low_price": row.get("low_price"),
                    "close_price": row.get("close_price"),
                    "prev_close_price": row.get("prev_close_price"),
                    "change_pct": row.get("change_pct"),
                    "volume": row.get("volume"),
                    "turnover": row.get("turnover"),
                    "source": row.get("source") or "akshare_sw",
                    "ingested_at": utcnow(),
                }
            )
        return self._bar_repo.bulk_upsert(values)

    def fill_industry(self, industry_code: str) -> dict[str, Any]:
        """补全单个行业日线并刷新质量快照（全历史重拉，幂等 upsert）。

        Args:
            industry_code: 申万一级行业代码，如 801010。

        Returns:
            {industry_code, fetched_rows, upserted_rows, 质量快照字段}。
        """
        code = self._ensure_universe_row(industry_code)
        rows = self._sw_client.fetch_daily(code)
        upserted = self._upsert_bars(code, rows)
        self._db.commit()
        snapshot = self._quality_snapshot(code)
        self._persist_quality_snapshots([snapshot])
        self._db.commit()
        return {
            "industry_code": code,
            "fetched_rows": len(rows),
            "upserted_rows": upserted,
            **snapshot,
        }

    def rebuild_industry(self, industry_code: str) -> dict[str, Any]:
        """全量重拉单个行业日线并刷新质量快照。

        先完整拉取成功，再在同一事务内清空旧行并写回；拉取失败时旧数据保留。

        Args:
            industry_code: 申万一级行业代码，如 801010。

        Returns:
            {industry_code, deleted_rows, upserted_rows, 质量快照字段}。
        """
        code = self._ensure_universe_row(industry_code)
        rows = self._sw_client.fetch_daily(code)
        deleted = 0
        if rows:
            deleted = self._bar_repo.delete_by_code(code)
            self._upsert_bars(code, rows)
            self._db.commit()
        snapshot = self._quality_snapshot(code)
        self._persist_quality_snapshots([snapshot])
        self._db.commit()
        return {
            "industry_code": code,
            "deleted_rows": deleted,
            "upserted_rows": len(rows),
            **snapshot,
        }

    def _ensure_universe_row(self, industry_code: str) -> str:
        """确保行业目录存在对应行；不存在且为已知申万一级行业时插入占位行。"""
        code = normalize_sw_code(industry_code)
        if self._universe_repo.find_by_code(code) is None:
            if code not in SW_L1_NAMES:
                raise ValueError(f"未知申万一级行业代码: {code}")
            self._universe_repo.bulk_upsert(
                [
                    {
                        "industry_code": code,
                        "name_cn": SW_L1_NAMES[code],
                        "classification": "sw",
                        "is_benchmark_excluded": code in SW_EXCLUDED_INDUSTRY_CODES,
                        "is_active": True,
                        "created_at": utcnow(),
                        "updated_at": utcnow(),
                    }
                ],
                update_cols=set(),
            )
        return code

    # ------------------------------------------------------------------
    # 行业成分
    # ------------------------------------------------------------------

    def refresh_membership(self, force: bool = False) -> dict[str, Any]:
        """同步申万分类历史并重建可用的行业成分事件。

        过滤规则：
        - start_date 不早于申万分类体系切换日（2014-02-21）；
        - update_time 不早于 2021 版体系回写日（2021-07-01）；
        - 6 位分类码前缀必须能映射到现行一级行业。

        Returns:
            {total, mapped, skipped_unmapped} 统计。
        """
        if not force:
            latest = self._membership_repo.latest_fetched_at()
            if latest is not None and (utcnow() - latest).days < _MEMBERSHIP_REFRESH_DAYS:
                return {"total": 0, "mapped": 0, "skipped_unmapped": 0, "skipped": True}
        raw = self._sw_client.fetch_classification_events()
        raw = raw[
            (raw["start_date"] >= SW_CLASSIFICATION_EPOCH)
            & (raw["update_time"] >= SW_CLASSIFICATION_REMAP_SINCE)
        ]
        total = len(raw)
        rows: list[dict[str, Any]] = []
        skipped = 0
        prefix_counts: dict[str, int] = {}
        for _, row in raw.iterrows():
            prefix = row["industry_code"][:2]
            industry_code = SW_CLASSIFICATION_PREFIX_TO_L1.get(prefix)
            if industry_code is None:
                skipped += 1
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
                continue
            rows.append(
                {
                    "stock_code": row["symbol"],
                    "industry_code": industry_code,
                    "start_date": row["start_date"],
                    "source": "sw_classification_2021",
                    "fetched_at": utcnow(),
                }
            )
        self._membership_repo.bulk_upsert(rows)
        self._db.commit()
        # 新出现的成分股票自动进入个股元数据占位行，保证个股页面名单不滞后
        try:
            from quant_etf_api.services.stock_data_service import (  # noqa: PLC0415
                StockDataService,
            )

            StockDataService(self._db).ensure_membership_stocks()
        except Exception:
            logger.warning(
                "成分股票元数据占位同步失败（可稍后执行 stock init-universe）", exc_info=True
            )
        logger.info(
            "行业成分同步完成: total=%s mapped=%s skipped=%s prefixes=%s",
            total,
            len(rows),
            skipped,
            prefix_counts,
        )
        return {"total": total, "mapped": len(rows), "skipped_unmapped": skipped}

    # ------------------------------------------------------------------
    # 个股收盘
    # ------------------------------------------------------------------

    def backfill_stock_close(
        self,
        start_date: str = _CLOSE_BACKFILL_DEFAULT_START,
        stock_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """回填全部成分股历史收盘价（Tushare 优先，回退 baostock/AkShare）。"""
        codes = stock_codes or sorted(
            {event.stock_code for event in self._membership_repo.find_all_events()}
        )
        if not codes:
            return {"codes": 0, "records": 0, "errors": []}
        total = 0
        errors: list[str] = []
        end = today_cn().strftime("%Y%m%d")
        for i, code in enumerate(codes, start=1):
            try:
                rows = []
                source = ""
                if self._tushare_stock.is_configured():
                    try:
                        rows = self._tushare_stock.fetch_history_close(
                            code, start_date, end
                        )
                        source = "tushare"
                    except Exception as exc:
                        logger.warning(
                            "个股 %s tushare 历史收盘拉取失败，回退 baostock: %s",
                            code,
                            exc,
                        )
                if not rows:
                    rows = self._close_client.fetch_history_baostock(code, start_date, end)
                    source = "baostock"
                if not rows:
                    rows = self._close_client.fetch_history_akshare(code, start_date, end)
                    source = "akshare"
                total += self._upsert_stock_close(code, rows, source)
                if i % 200 == 0:
                    self._db.commit()
                    logger.info("个股收盘回填进度: %s/%s", i, len(codes))
            except Exception as e:
                errors.append(f"{code}: {type(e).__name__}: {e}")
                logger.warning("个股 %s 收盘回填失败: %s", code, e)
        self._db.commit()
        return {"codes": len(codes), "records": total, "errors": errors}

    def refresh_stock_close_snapshot(self, trade_date: date) -> int:
        """用 Tushare 全市场日线（优先）更新指定交易日收盘价。

        Tushare daily 收盘后发布当日全 A（沪深京）收盘；盘中/发布延迟时返回
        空数据，此时回退 AkShare 全 A 快照，保证当日数据不缺失。
        """
        rows: list[dict[str, Any]] = []
        if self._tushare_stock.is_configured():
            try:
                rows = self._tushare_stock.fetch_close_by_trade_date(trade_date)
            except Exception as exc:
                logger.warning(
                    "tushare 全市场收盘拉取失败（%s），回退 AkShare 快照",
                    exc,
                )
        if rows:
            # Tushare daily 不含全天停牌个股；对活跃名单中缺失的代码用 AkShare
            # 快照补充，避免质量检查把停牌交易日误报为数据缺失
            active_codes = {
                row[0]
                for row in self._db.query(StockUniverseModel.stock_code)
                .filter(StockUniverseModel.is_active.is_(True))
                .all()
            }
            covered = {row["stock_code"] for row in rows}
            missing = sorted(active_codes - covered)
            if missing:
                try:
                    spot = self._close_client.fetch_all_close_snapshot()
                    spot_by = {row["stock_code"]: row for row in spot}
                    supplement = [spot_by[code] for code in missing if code in spot_by]
                    if supplement:
                        logger.info(
                            "Tushare 收盘缺口 %d 只（含停牌股），由 AkShare 快照补充 %d 只",
                            len(missing),
                            len(supplement),
                        )
                        rows.extend(supplement)
                except Exception as exc:
                    logger.warning("AkShare 快照补充停牌股失败（忽略）: %s", exc)
        if not rows:
            rows = self._close_client.fetch_all_close_snapshot()
        values = [
            {
                "trade_date": trade_date,
                "stock_code": row["stock_code"],
                "close": row["close"],
                "source": row.get("source") or "akshare_em",
                "ingested_at": utcnow(),
            }
            for row in rows
        ]
        count = self._close_repo.bulk_upsert(values)
        self._db.commit()
        return count

    def _upsert_stock_close(self, stock_code: str, rows: list[dict[str, Any]], source: str) -> int:
        """将个股收盘行幂等写入 stock_daily_close。"""
        if not rows:
            return 0
        values = [
            {
                "trade_date": row["trade_date"],
                "stock_code": stock_code,
                "close": row["close"],
                "source": source,
                "ingested_at": utcnow(),
            }
            for row in rows
        ]
        return self._close_repo.bulk_upsert(values)

    # ------------------------------------------------------------------
    # 日频入口
    # ------------------------------------------------------------------

    def run_daily_ingest(self, target_date: date | None = None) -> dict[str, Any]:
        """执行行业轮动子系统日频数据摄取。

        Args:
            target_date: 期望补数的交易日；None 时以行业日线最新日期为准。

        Returns:
            结构化运行指标：{target_date, bars, membership, stock_snapshot, quality}。
            行业日线逐行业失败会进入 bars.errors；成分/个股快照阶段失败记录为
            对应字段的 error 但不中断整体执行（可由 CLI/页面单独重跑补齐）。
        """
        result: dict[str, Any] = {"target_date": None}
        result["bars"] = self.refresh_industry_bars_incremental()
        latest = self._bar_repo.latest_trade_date()
        if latest is None:
            return result
        result["target_date"] = latest
        try:
            result["membership"] = self.refresh_membership()
        except Exception as exc:
            result["membership"] = {"error": f"{type(exc).__name__}: {exc}"}
            logger.warning(
                "行业成分刷新失败，跳过（可稍后执行 backfill-membership）", exc_info=True
            )
        try:
            result["stock_snapshot"] = {
                "records": self.refresh_stock_close_snapshot(target_date or latest)
            }
        except Exception as exc:
            result["stock_snapshot"] = {"error": f"{type(exc).__name__}: {exc}"}
            logger.warning(
                "个股收盘快照刷新失败，跳过（可稍后执行 stock fill --all）", exc_info=True
            )
        # 日频摄取末尾重算 31 个行业质量快照，保证总览与列表页无需手动初检
        try:
            result["quality"] = self.bulk_quality()
        except Exception as exc:
            result["quality"] = {"error": f"{type(exc).__name__}: {exc}"}
            logger.warning("行业日频摄取后质量快照重算失败", exc_info=True)
        return result

    # ------------------------------------------------------------------
    # 交易日历与质量快照
    # ------------------------------------------------------------------

    def _trading_days(self, start: date, end: date) -> list[date]:
        """获取 [start, end] 内的交易日，优先 AkShare 日历，回退行业日线。"""
        if (
            self._trading_cache is not None
            and self._trading_cache[0] <= start
            and end <= self._trading_cache[1]
        ):
            _cached_start, _cached_end, cached_days = self._trading_cache
            return [d for d in cached_days if start <= d <= end]

        trading_set = self._calendar.get_trading_days_set()
        if trading_set is not None:
            days = sorted(d for d in trading_set if start <= d <= end)
            if days:
                self._trading_cache = (start, end, days)
                return days

        days = self._bar_repo.find_market_trading_dates(start, end)
        if days:
            self._trading_cache = (start, end, days)
            return days
        raise ValueError("交易日历不可用：AkShare 日历为空且行业日线无覆盖，无法进行质量检查")

    def _latest_trading_day(self) -> date:
        """获取不晚于今天的最近交易日（严格模式，不做周末近似）。"""
        trading_set = self._calendar.get_trading_days_set()
        if trading_set is not None:
            candidates = sorted(d for d in trading_set if d <= today_cn())
            if candidates:
                return candidates[-1]
        epoch = date(2013, 1, 1)
        days = self._bar_repo.find_market_trading_dates(epoch, today_cn())
        if days:
            return days[-1]
        raise ValueError("交易日历不可用，无法确定最近交易日")

    def _quality_snapshot(self, industry_code: str) -> dict[str, Any]:
        """计算单个行业日线质量快照（只读，不落库）。"""
        row = self._universe_repo.find_by_code(industry_code)
        if row is None:
            raise ValueError(
                f"行业 {industry_code} 不在 industry_universe 中，请先执行 industry init-universe"
            )
        actual = self._bar_repo.find_trade_dates_by_code(industry_code)
        if not actual:
            trading_days: list[date] = []
            end = self._latest_trading_day()
        else:
            end = self._latest_trading_day()
            trading_days = self._trading_days(actual[0], end)
        result = compute_industry_bar_quality(
            actual_dates=actual,
            trading_days=trading_days,
            expected_end=end,
        )
        return {
            "industry_code": industry_code,
            "data_start_date": result["data_start_date"],
            "data_end_date": result["data_end_date"],
            "bar_count": result["bar_count"],
            "missing_day_count": result["missing_day_count"],
        }

    def quality_check(self, industry_code: str) -> dict[str, Any]:
        """检查单个行业日线质量并落库快照（页面/后台任务入口）。"""
        snapshot = self._quality_snapshot(industry_code)
        self._persist_quality_snapshots([snapshot])
        self._db.commit()
        return snapshot

    def bulk_quality(self, industry_codes: list[str] | None = None) -> dict[str, Any]:
        """批量质量检查并落库快照（全部行业或指定列表，仅 CLI/日频摄取使用）。"""
        target_codes = self._resolve_codes(industry_codes)
        errors: list[str] = []
        snapshots: list[dict[str, Any]] = []
        for code in target_codes:
            try:
                snapshots.append(self._quality_snapshot(code))
            except Exception as exc:
                errors.append(f"{code}: {type(exc).__name__}: {exc}")
                logger.warning("行业 %s 质量检查失败: %s", code, exc)
        if snapshots:
            self._persist_quality_snapshots(snapshots)
            self._db.commit()
        return {"codes": len(target_codes), "checked": len(snapshots), "errors": errors}

    def _persist_quality_snapshots(self, snapshots: list[dict[str, Any]]) -> None:
        """把行业质量快照批量写回 industry_universe 质量列。"""
        if not snapshots:
            return
        # name_cn 无数据库默认值：INSERT..ON CONFLICT 也会校验新行非空，
        # 因此必须把当前目录行名称合并进写入行
        current_rows = {
            row.industry_code: row for row in self._universe_repo.find_all()
        }
        rows: list[dict[str, Any]] = []
        for snap in snapshots:
            current = current_rows.get(snap["industry_code"])
            rows.append(
                {
                    "industry_code": snap["industry_code"],
                    "name_cn": (
                        current.name_cn
                        if current is not None
                        else SW_L1_NAMES.get(snap["industry_code"], "")
                    ),
                    "data_start_date": snap["data_start_date"],
                    "data_end_date": snap["data_end_date"],
                    "bar_count": snap["bar_count"],
                    "missing_day_count": snap["missing_day_count"],
                    "quality_checked_at": utcnow(),
                    "updated_at": utcnow(),
                }
            )
        self._universe_repo.bulk_upsert(
            rows,
            update_cols={
                "data_start_date",
                "data_end_date",
                "bar_count",
                "missing_day_count",
                "quality_checked_at",
                "updated_at",
            },
        )

    def _membership_ref_date(self) -> date:
        """返回成分计数参考日：优先行业日线最新交易日，缺数据时用今天。"""
        return self._bar_repo.latest_trade_date() or today_cn()

    def list_summary(self) -> list[IndustrySummaryItem]:
        """聚合行业目录、当前成分数、质量快照与最新一根行情，供管理列表页。"""
        rows = self._universe_repo.find_all()
        member_counts = self._membership_repo.current_membership_counts(
            self._membership_ref_date()
        )
        latest_bars = self._bar_repo.find_latest_bars()
        items: list[IndustrySummaryItem] = []
        for row in rows:
            bar = latest_bars.get(row.industry_code)
            items.append(
                IndustrySummaryItem(
                    industry_code=row.industry_code,
                    name_cn=row.name_cn,
                    is_benchmark_excluded=row.is_benchmark_excluded,
                    member_count=member_counts.get(row.industry_code, 0),
                    data_start_date=row.data_start_date,
                    data_end_date=row.data_end_date,
                    bar_count=row.bar_count,
                    missing_day_count=row.missing_day_count,
                    quality_checked_at=row.quality_checked_at,
                    latest_trade_date=bar.trade_date if bar else None,
                    latest_close=bar.close_price if bar else None,
                    latest_change_pct=bar.change_pct if bar else None,
                )
            )
        return items

    def industry_quality_detail(self, industry_code: str) -> IndustryQualityDetail:
        """计算行业详情页质量（快照 + 全历史日线字段完整性）。"""
        row = self._universe_repo.find_by_code(industry_code)
        if row is None:
            raise ValueError(f"行业 {industry_code} 不在 industry_universe 中")
        bars = self._bar_repo.find_by_code(industry_code)
        total = len(bars)
        missing_open = sum(1 for b in bars if b.open_price is None)
        missing_high = sum(1 for b in bars if b.high_price is None)
        missing_low = sum(1 for b in bars if b.low_price is None)
        missing_close = sum(1 for b in bars if b.close_price is None)
        incomplete_rows = sum(
            1
            for b in bars
            if b.open_price is None or b.high_price is None or b.low_price is None
        )
        change_pct_null = sum(1 for b in bars if b.change_pct is None)
        return IndustryQualityDetail(
            industry_code=industry_code,
            data_start_date=row.data_start_date,
            data_end_date=row.data_end_date,
            bar_count=row.bar_count,
            missing_day_count=row.missing_day_count,
            quality_checked_at=row.quality_checked_at,
            total=total,
            min_date=bars[0].trade_date if bars else None,
            max_date=bars[-1].trade_date if bars else None,
            missing_open=missing_open,
            missing_high=missing_high,
            missing_low=missing_low,
            missing_close=missing_close,
            incomplete_rows=incomplete_rows,
            incomplete_ratio=round(incomplete_rows / total, 4) if total else 0.0,
            change_pct_null=change_pct_null,
            change_pct_null_rate=round(change_pct_null / total, 4) if total else 0.0,
        )

    def _resolve_codes(self, codes: list[str] | None) -> list[str]:
        """解析行业代码列表；为空时使用全部启用行业。"""
        if codes:
            return sorted(set(codes))
        return self._universe_repo.find_active_codes()
