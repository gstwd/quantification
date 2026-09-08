"""个股数据管理服务：元数据同步、质量快照、单股补全/重拉与 CLI 批量入口。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.stocks.quality import compute_stock_quality
from quant_etf_api.infra.clients.stock_close_client import (
    StockCloseClient,
    _market_prefix,
)
from quant_etf_api.infra.clients.stock_metadata_client import StockMetadataClient
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.repositories.industry import (
    IndustryDailyBarRepository,
    IndustryMembershipEventRepository,
    IndustryUniverseRepository,
    StockDailyCloseRepository,
)
from quant_etf_api.infra.db.repositories.stock import StockUniverseRepository
from quant_etf_api.infra.trading_calendar import TradingCalendar
from quant_etf_api.infra.time import today_cn
from quant_etf_api.schemas.stock import StockSummary

logger = logging.getLogger(__name__)

# 与旧版回填一致的历史回填起点；早于此的行情对申万成分研究无意义
_STOCK_FETCH_EPOCH = date(2013, 1, 1)


class StockDataService:
    """个股数据服务。

    职责范围：
    - stock_universe 元数据同步（申万成分范围 + 交易所名单名称/上市日/退市状态）；
    - 按交易日历的质量快照检查与落库；
    - 单股日线补全/全量重拉（页面与后台任务调用）；
    - 全市场批量质量检查与补全（仅 CLI 调用）。
    """

    def __init__(self, db: Session) -> None:
        """初始化个股数据服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._universe_repo = StockUniverseRepository(db)
        self._close_repo = StockDailyCloseRepository(db)
        self._membership_repo = IndustryMembershipEventRepository(db)
        self._bar_repo = IndustryDailyBarRepository(db)
        self._industry_repo = IndustryUniverseRepository(db)
        self._metadata_client = StockMetadataClient()
        self._close_client = StockCloseClient()
        self._calendar = TradingCalendar()
        self._trading_cache: tuple[date, date, list[date]] | None = None

    # ------------------------------------------------------------------
    # 元数据同步
    # ------------------------------------------------------------------

    def ensure_membership_stocks(self) -> int:
        """确保申万成分事件中的股票都存在于 stock_universe（占位行）。"""
        events = self._membership_repo.find_all_events()
        codes = sorted({event.stock_code for event in events})
        existing = {row.stock_code for row in self._universe_repo.find_all(codes=codes)}
        missing_codes = [code for code in codes if code not in existing]
        if missing_codes:
            placeholders = [
                {
                    "stock_code": code,
                    "name_cn": "",
                    "source": "sw_membership",
                    "created_at": utcnow(),
                    "updated_at": utcnow(),
                }
                for code in missing_codes
            ]
            self._universe_repo.bulk_upsert(placeholders, update_cols=set())
            self._db.commit()
        return len(missing_codes)

    def sync_universe(self) -> dict[str, int]:
        """同步个股元数据：申万成分范围 + 交易所名单 + 当前行业归属。"""
        events = self._membership_repo.find_all_events()
        membership_codes = sorted({event.stock_code for event in events})
        industry_map = self._current_industry_map()

        added = self.ensure_membership_stocks()
        existing_rows = {
            row.stock_code: row for row in self._universe_repo.find_all(codes=membership_codes)
        }
        basics = self._metadata_client.fetch_exchange_basics()
        basics_by_code = {row["stock_code"]: row for row in basics}

        updates: list[dict[str, Any]] = []
        for code in membership_codes:
            current = existing_rows.get(code)
            if current is None:
                continue
            basic = basics_by_code.get(code, {})
            industry = industry_map.get(code)
            delist_date = basic.get("delist_date") or current.delist_date
            if basic and basic.get("is_active") is True and not basic.get("delist_date"):
                # 出现在活跃名单且无退市日期时，清除历史退市标记（例如恢复上市）
                delist_date = None
            updates.append(
                {
                    "stock_code": code,
                    "name_cn": basic.get("name_cn") or current.name_cn,
                    "industry_code": industry if industry is not None else current.industry_code,
                    "ipo_date": basic.get("ipo_date") or current.ipo_date,
                    "delist_date": delist_date,
                    "is_active": basic.get("is_active", current.is_active),
                    "source": basic.get("source") or current.source,
                    "updated_at": utcnow(),
                }
            )
        self._universe_repo.bulk_upsert(
            updates,
            update_cols={
                "name_cn",
                "industry_code",
                "ipo_date",
                "delist_date",
                "is_active",
                "source",
                "updated_at",
            },
        )
        self._db.commit()
        return {"codes": len(membership_codes), "added": added, "updated": len(updates)}

    def _current_industry_map(self) -> dict[str, str]:
        """返回股票代码 → 当前有效申万一级行业（start_date 不大于今天的最新事件）。"""
        events = self._membership_repo.find_events_until(today_cn())
        best: dict[str, tuple[date, str]] = {}
        for event in events:
            current = best.get(event.stock_code)
            if current is None or (event.start_date, event.industry_code) > current:
                best[event.stock_code] = (event.start_date, event.industry_code)
        return {code: industry for code, (_, industry) in best.items()}

    # ------------------------------------------------------------------
    # 交易日历
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
        days = self._bar_repo.find_market_trading_dates(_STOCK_FETCH_EPOCH, today_cn())
        if days:
            return days[-1]
        raise ValueError("交易日历不可用，无法确定最近交易日")

    # ------------------------------------------------------------------
    # 质量快照
    # ------------------------------------------------------------------

    def _expected_bounds(
        self,
        row: Any,
        actual: list[date],
        start_override: date | None = None,
    ) -> tuple[date, date]:
        """计算单只股票的期望统计区间（上市日→退市日/最近交易日）。"""
        if row.ipo_date is not None:
            start = row.ipo_date
        elif start_override is not None:
            start = start_override
        elif actual:
            start = actual[0]
        else:
            start = _STOCK_FETCH_EPOCH
        end = row.delist_date if row.delist_date is not None else self._latest_trading_day()
        if start > end:
            start, end = end, start
        return start, end

    def _quality_snapshot(
        self,
        stock_code: str,
        start_override: date | None = None,
    ) -> dict[str, Any]:
        """计算单只股票质量快照（只读，不落库）。"""
        row = self._universe_repo.find_by_code(stock_code)
        if row is None:
            raise ValueError(
                f"股票 {stock_code} 不在 stock_universe 中，请先执行 stock init-universe"
            )
        actual = self._close_repo.find_trade_dates_by_code(stock_code)
        start, end = self._expected_bounds(row, actual, start_override=start_override)
        trading_days = self._trading_days(start, end)
        result = compute_stock_quality(
            actual_dates=actual,
            trading_days=trading_days,
            expected_start=start,
            expected_end=end,
        )
        return {
            "stock_code": stock_code,
            "data_start_date": result["data_start_date"],
            "data_end_date": result["data_end_date"],
            "bar_count": result["bar_count"],
            "missing_day_count": result["missing_day_count"],
        }

    def quality_check(self, stock_code: str) -> dict[str, Any]:
        """检查单只股票质量并落库快照（页面/后台任务入口）。"""
        snapshot = self._quality_snapshot(stock_code)
        self._persist_quality_snapshots([snapshot])
        self._db.commit()
        return snapshot

    def bulk_quality(self, codes: list[str] | None = None) -> dict[str, Any]:
        """批量质量检查并落库快照（仅 CLI）。"""
        target_codes = self._resolve_codes(codes)
        errors: list[str] = []
        snapshots: list[dict[str, Any]] = []
        total = len(target_codes)
        for i, code in enumerate(target_codes, start=1):
            try:
                snapshots.append(self._quality_snapshot(code))
            except Exception as exc:
                errors.append(f"{code}: {type(exc).__name__}: {exc}")
                logger.warning("个股 %s 质量检查失败: %s", code, exc)
            if len(snapshots) >= 200:
                self._persist_quality_snapshots(snapshots)
                self._db.commit()
                snapshots = []
            if i % 200 == 0:
                logger.info("个股质量检查进度: %s/%s", i, total)
        if snapshots:
            self._persist_quality_snapshots(snapshots)
            self._db.commit()
        return {
            "codes": total,
            "checked": total - len(errors),
            "errors": errors,
        }

    def _persist_quality_snapshots(self, snapshots: list[dict[str, Any]]) -> None:
        """把质量快照批量写回 stock_universe 质量列。"""
        if not snapshots:
            return
        rows = [
            {
                "stock_code": snap["stock_code"],
                "data_start_date": snap["data_start_date"],
                "data_end_date": snap["data_end_date"],
                "bar_count": snap["bar_count"],
                "missing_day_count": snap["missing_day_count"],
                "quality_checked_at": utcnow(),
                "updated_at": utcnow(),
            }
            for snap in snapshots
        ]
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

    # ------------------------------------------------------------------
    # 数据拉取与补全
    # ------------------------------------------------------------------

    def _resolve_codes(self, codes: list[str] | None) -> list[str]:
        """解析目标股票列表；为空时优先 stock_universe，其次申万成分。"""
        if codes:
            return sorted(set(codes))
        universe_codes = self._universe_repo.find_codes()
        if universe_codes:
            return universe_codes
        events = self._membership_repo.find_all_events()
        return sorted({event.stock_code for event in events})

    def list_stocks(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        keyword: str | None = None,
        industry_code: str | None = None,
        status: str | None = None,
    ) -> tuple[list[StockSummary], int]:
        """分页查询个股列表摘要（含行业名称映射）。"""
        rows, total = self._universe_repo.search(
            offset=offset,
            limit=limit,
            keyword=keyword,
            industry_code=industry_code,
            status=status,
        )
        industry_names = {
            row.industry_code: row.name_cn for row in self._industry_repo.find_all_active()
        }
        items = [
            StockSummary(
                stock_code=row.stock_code,
                name_cn=row.name_cn,
                industry_code=row.industry_code,
                industry_name=industry_names.get(row.industry_code) if row.industry_code else None,
                ipo_date=row.ipo_date,
                delist_date=row.delist_date,
                is_active=row.is_active,
                data_start_date=row.data_start_date,
                data_end_date=row.data_end_date,
                bar_count=row.bar_count,
                missing_day_count=row.missing_day_count,
                quality_checked_at=row.quality_checked_at,
            )
            for row in rows
        ]
        return items, total

    def _ensure_stock_row(self, stock_code: str) -> None:
        """股票不存在于 stock_universe 时插入占位行。"""
        if self._universe_repo.find_by_code(stock_code) is None:
            self._universe_repo.bulk_upsert(
                [
                    {
                        "stock_code": stock_code,
                        "name_cn": "",
                        "source": "sw_membership",
                        "created_at": utcnow(),
                        "updated_at": utcnow(),
                    }
                ],
                update_cols=set(),
            )
            self._db.commit()

    def _fetch_window(
        self,
        row: Any,
        actual: list[date],
        start_override: date | None = None,
    ) -> tuple[date, date]:
        """计算拉取窗口：从上市日/默认起点到退市日/最近交易日。"""
        start, end = self._expected_bounds(row, actual, start_override=start_override)
        if actual:
            start = min(start, actual[0])
        return start, end

    def _fetch_rows(
        self,
        stock_code: str,
        start: date,
        end: date,
        *,
        in_session: bool,
    ) -> list[dict[str, Any]]:
        """按股票代码拉取历史收盘，主源失败后依次回退东财/腾讯 AkShare。"""
        start_s = start.strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")
        prefix = _market_prefix(stock_code)
        failures: list[str] = []
        rows: list[dict[str, Any]] = []

        if prefix in ("sh", "sz"):
            # 沪深主源：baostock
            try:
                if in_session:
                    rows = self._close_client.fetch_history_baostock_logged_in(
                        stock_code, start_s, end_s
                    )
                else:
                    rows = self._close_client.fetch_history_baostock(
                        stock_code, start_s, end_s
                    )
                if rows:
                    return [dict(row, source="baostock") for row in rows]
                failures.append("baostock 返回空")
            except Exception as exc:
                failures.append(f"baostock({type(exc).__name__}: {exc})")
                logger.warning("个股 %s baostock 拉取失败: %s", stock_code, exc)

        # 东财 AkShare（北交所直接使用；沪深在 baostock 失败/空结果后兜底）
        try:
            rows = self._close_client.fetch_history_akshare(stock_code, start_s, end_s)
            if rows:
                return [dict(row, source="akshare") for row in rows]
            failures.append("东财 AkShare 返回空")
        except Exception as exc:
            failures.append(f"东财 AkShare({type(exc).__name__}: {exc})")
            logger.warning("个股 %s 东财 AkShare 拉取失败: %s", stock_code, exc)

        # 腾讯 AkShare（东财在代理/限流场景不可达时的备用源）
        try:
            rows = self._close_client.fetch_history_tencent(stock_code, start_s, end_s)
            if rows:
                return [dict(row, source="tencent") for row in rows]
            failures.append("腾讯 AkShare 返回空")
        except Exception as exc:
            failures.append(f"腾讯 AkShare({type(exc).__name__}: {exc})")
            logger.warning("个股 %s 腾讯 AkShare 拉取失败: %s", stock_code, exc)

        raise RuntimeError("; ".join(failures) or "所有数据源均未尝试")

    def _upsert_rows(self, stock_code: str, rows: list[dict[str, Any]]) -> int:
        """把收盘行幂等写入 stock_daily_close。"""
        if not rows:
            return 0
        values = [
            {
                "trade_date": row["trade_date"],
                "stock_code": stock_code,
                "close": row["close"],
                "source": row.get("source") or "baostock",
                "ingested_at": utcnow(),
            }
            for row in rows
        ]
        return self._close_repo.bulk_upsert(values)

    def fill_stock(
        self,
        stock_code: str,
        *,
        in_session: bool = False,
        start_date: date | None = None,
    ) -> dict[str, Any]:
        """补全单只股票日线到最近交易日并刷新质量快照。"""
        self._ensure_stock_row(stock_code)
        row = self._universe_repo.find_by_code(stock_code)
        if row is None:
            raise RuntimeError(f"股票 {stock_code} 元数据初始化失败")
        actual = self._close_repo.find_trade_dates_by_code(stock_code)
        start, end = self._fetch_window(row, actual, start_override=start_date)
        rows = self._fetch_rows(stock_code, start, end, in_session=in_session)
        upserted = self._upsert_rows(stock_code, rows)
        self._db.commit()
        snapshot = self._quality_snapshot(stock_code, start_override=start_date)
        self._persist_quality_snapshots([snapshot])
        self._db.commit()
        return {
            "stock_code": stock_code,
            "fetched_rows": len(rows),
            "upserted_rows": upserted,
            **snapshot,
        }

    def rebuild_stock(
        self,
        stock_code: str,
        *,
        in_session: bool = False,
        start_date: date | None = None,
    ) -> dict[str, Any]:
        """全量重拉单只股票：先完整拉取，成功后清空旧行并写回。"""
        self._ensure_stock_row(stock_code)
        row = self._universe_repo.find_by_code(stock_code)
        if row is None:
            raise RuntimeError(f"股票 {stock_code} 元数据初始化失败")
        actual = self._close_repo.find_trade_dates_by_code(stock_code)
        start, end = self._fetch_window(row, actual, start_override=start_date)
        rows = self._fetch_rows(stock_code, start, end, in_session=in_session)
        deleted = 0
        if rows:
            deleted = self._close_repo.delete_by_code(stock_code)
            self._upsert_rows(stock_code, rows)
            self._db.commit()
        snapshot = self._quality_snapshot(stock_code, start_override=start_date)
        self._persist_quality_snapshots([snapshot])
        self._db.commit()
        return {
            "stock_code": stock_code,
            "deleted_rows": deleted,
            "upserted_rows": len(rows),
            **snapshot,
        }

    def bulk_fill(
        self,
        *,
        codes: list[str] | None = None,
        start_date: date | None = None,
        only_missing: bool = False,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        """批量补全/重拉股票日线（仅 CLI 使用，复用单次 baostock 会话）。"""
        target_codes = self._resolve_codes(codes)
        if only_missing and not codes:
            rows = self._universe_repo.find_all(codes=target_codes)
            target_codes = [
                row.stock_code
                for row in rows
                if row.bar_count is None or row.bar_count == 0 or (row.missing_day_count or 0) > 0
            ]
        records = 0
        errors: list[str] = []
        other_codes = [code for code in target_codes if _market_prefix(code) not in ("sh", "sz")]
        shsz_codes = [code for code in target_codes if _market_prefix(code) in ("sh", "sz")]
        records += self._bulk_process_codes(
            other_codes,
            in_session=False,
            rebuild=rebuild,
            start_date=start_date,
            errors=errors,
        )
        if shsz_codes:
            try:
                with self._close_client.baostock_session():
                    records += self._bulk_process_codes(
                        shsz_codes,
                        in_session=True,
                        rebuild=rebuild,
                        start_date=start_date,
                        errors=errors,
                    )
            except Exception as exc:
                # 会话级登录失败时退化为逐只登录方式，保证 AkShare 兜底仍可用
                logger.warning("baostock 批量会话失败，退化逐只登录: %s", exc)
                records += self._bulk_process_codes(
                    shsz_codes,
                    in_session=False,
                    rebuild=rebuild,
                    start_date=start_date,
                    errors=errors,
                )
        return {
            "codes": len(target_codes),
            "ok": len(target_codes) - len(errors),
            "records": records,
            "rebuild": rebuild,
            "errors": errors,
        }

    def _bulk_process_codes(
        self,
        codes: list[str],
        *,
        in_session: bool,
        rebuild: bool,
        start_date: date | None,
        errors: list[str],
    ) -> int:
        """批量处理一组股票并就地累积错误列表，返回成功写入行数。"""
        records = 0
        for i, code in enumerate(codes, start=1):
            try:
                if rebuild:
                    result = self.rebuild_stock(code, in_session=in_session, start_date=start_date)
                else:
                    result = self.fill_stock(code, in_session=in_session, start_date=start_date)
                records += int(result.get("upserted_rows") or 0)
            except Exception as exc:
                errors.append(f"{code}: {type(exc).__name__}: {exc}")
                logger.warning("个股 %s 批量补全失败: %s", code, exc)
        return records
