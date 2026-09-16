"""个股数据管理服务：元数据同步、单股补全/重拉与 CLI 批量入口。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.tushare_market import TushareStockClient
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.repositories.industry import (
    IndustryDailyBarRepository,
    IndustryUniverseRepository,
    StockDailyCloseRepository,
)
from quant_etf_api.infra.db.repositories.data_health import DataHealthSnapshotRepository
from quant_etf_api.infra.db.repositories.stock import StockUniverseRepository
from quant_etf_api.infra.db.repositories.stock_daily import (
    StockDailyBasicRepository,
    StockMoneyflowRepository,
)
from quant_etf_api.infra.trading_calendar import TradingCalendar
from quant_etf_api.infra.time import today_cn
from quant_etf_api.schemas.stock import StockSummary

logger = logging.getLogger(__name__)

# Tushare 个股统一起点；2013 年前旧 close-only 行将被清理
_STOCK_FETCH_EPOCH = date(2013, 1, 1)


class StockDataService:
    """个股数据服务。

    职责范围：
    - stock_universe 元数据同步（Tushare 沪深 A 股名单、ts_code/市场/交易所）；
    - 按交易日历的质量快照检查与落库；
    - Tushare 日线、复权因子、每日指标、资金流向的全市场与单股补全/重拉；
    - 全市场批量质量检查、历史回填与旧源数据清理（仅 CLI 调用）。
    """

    def __init__(self, db: Session) -> None:
        """初始化个股数据服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._universe_repo = StockUniverseRepository(db)
        self._health_repo = DataHealthSnapshotRepository(db)
        self._close_repo = StockDailyCloseRepository(db)
        self._bar_repo = IndustryDailyBarRepository(db)
        self._industry_repo = IndustryUniverseRepository(db)
        self._basic_repo = StockDailyBasicRepository(db)
        self._moneyflow_repo = StockMoneyflowRepository(db)
        self._tushare_client = TushareStockClient()
        self._calendar = TradingCalendar()
        self._trading_cache: tuple[date, date, list[date]] | None = None

    # ------------------------------------------------------------------
    # 元数据同步
    # ------------------------------------------------------------------

    def sync_universe(self) -> dict[str, int]:
        """同步个股元数据：仅使用 Tushare 沪深 A 股全量名单。

        Tushare 名单按 6 位代码去重；同一代码出现多个 ts_code 时优先保留
        上市状态、最新上市日期的记录，并把冲突数量写入返回指标。申万行业
        成分表仅作为研究数据输入，不会在此创建证券基础信息或引入北交所代码。

        Returns:
            {codes, added, updated, conflicts}。
        """
        basics = self._fetch_universe_basics()
        chosen: dict[str, dict[str, Any]] = {}
        conflicts = 0
        for row in basics:
            code = row["stock_code"]
            current = chosen.get(code)
            if current is None:
                chosen[code] = row
                continue
            conflicts += 1
            if self._basic_rank(row) > self._basic_rank(current):
                chosen[code] = row

        existing_rows = {
            row.stock_code: row for row in self._universe_repo.find_all(codes=sorted(chosen))
        }
        added = len(set(chosen) - set(existing_rows))

        updates: list[dict[str, Any]] = []
        for code in sorted(chosen):
            basic = chosen[code]
            current = existing_rows.get(code)
            delist_date = basic.get("delist_date") or (
                current.delist_date if current is not None else None
            )
            if basic and basic.get("is_active") is True and not basic.get("delist_date"):
                # 出现在活跃名单且无退市日期时，清除历史退市标记（例如恢复上市）
                delist_date = None
            updates.append(
                {
                    "stock_code": code,
                    "name_cn": basic.get("name_cn")
                    or (current.name_cn if current is not None else ""),
                    "industry_code": None,
                    "ts_code": basic.get("ts_code"),
                    "market": basic.get("market"),
                    "exchange": basic.get("exchange"),
                    "ipo_date": basic.get("ipo_date")
                    or (current.ipo_date if current is not None else None),
                    "delist_date": delist_date,
                    "is_active": basic.get(
                        "is_active",
                        current.is_active if current is not None else True,
                    ),
                    "source": basic.get("source") or "tushare",
                    "created_at": current.created_at if current is not None else utcnow(),
                    "updated_at": utcnow(),
                }
            )
        self._universe_repo.bulk_upsert(
            updates,
            update_cols={
                "name_cn",
                "industry_code",
                "ts_code",
                "market",
                "exchange",
                "ipo_date",
                "delist_date",
                "is_active",
                "source",
                "updated_at",
            },
        )
        self._db.commit()
        return {
            "codes": len(chosen),
            "added": added,
            "updated": len(updates),
            "conflicts": conflicts,
        }

    @staticmethod
    def _basic_rank(row: dict[str, Any]) -> tuple[int, date, str]:
        """返回 Tushare 基础信息行的去重排序键。

        Args:
            row: Tushare 基础信息行。

        Returns:
            (是否活跃, 上市日期, ts_code)，元组越大越应保留。
        """
        return (
            1 if row.get("is_active") else 0,
            row.get("ipo_date") or date.min,
            str(row.get("ts_code") or ""),
        )

    def _fetch_universe_basics(self) -> list[dict[str, Any]]:
        """拉取 Tushare stock_basic 沪深 A 股全量名单。

        Returns:
            [{stock_code, ts_code, name_cn, market, exchange, ipo_date,
            delist_date, is_active, source}]。

        Raises:
            RuntimeError: 未配置 Tushare Token 或返回空名单时抛出；个股数据
                已改为 Tushare 独占，不再回退交易所/AkShare。
        """
        if not self._tushare_client.is_configured():
            raise RuntimeError("未配置 TUSHARE_TOKEN，个股数据已切换为 Tushare 独占")
        rows = self._tushare_client.fetch_stock_basics()
        if not rows:
            raise RuntimeError("Tushare stock_basic 返回空名单，拒绝更新 stock_universe")
        return rows

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

    def latest_trading_day(self) -> date:
        """返回不晚于今天的最近交易日（公开入口，供 CLI 默认区间使用）。"""
        return self._latest_trading_day()

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
            # 2013 年前旧 close-only 行会被清理，期望区间统一下限到 Tushare 起点
            start = max(row.ipo_date, _STOCK_FETCH_EPOCH)
        elif start_override is not None:
            start = max(start_override, _STOCK_FETCH_EPOCH)
        elif actual:
            start = max(actual[0], _STOCK_FETCH_EPOCH)
        else:
            start = _STOCK_FETCH_EPOCH
        end = row.delist_date if row.delist_date is not None else self._latest_trading_day()
        if end < _STOCK_FETCH_EPOCH:
            # 2013 年前已退市的股票在清理后没有可维护数据，返回空区间
            return _STOCK_FETCH_EPOCH, _STOCK_FETCH_EPOCH - timedelta(days=1)
        if start > end:
            start, end = end, start
        return start, end

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
        health = self._health_repo.find_by_dataset_and_partitions(
            "stock_daily_close", [row.stock_code for row in rows]
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
                data_start_date=health.get(row.stock_code).earliest_date
                if health.get(row.stock_code)
                else None,
                data_end_date=health.get(row.stock_code).latest_date
                if health.get(row.stock_code)
                else None,
                bar_count=health.get(row.stock_code).record_count
                if health.get(row.stock_code)
                else None,
                missing_day_count=health.get(row.stock_code).missing_count
                if health.get(row.stock_code)
                else None,
                quality_checked_at=health.get(row.stock_code).last_checked_at
                if health.get(row.stock_code)
                else None,
            )
            for row in rows
        ]
        return items, total

    def _ensure_stock_row(self, stock_code: str) -> None:
        """确认单股任务目标属于已同步的 Tushare 沪深 A 股目录。"""
        if self._universe_repo.find_by_code(stock_code) is None:
            raise ValueError(
                f"股票 {stock_code} 不在已同步的 Tushare 沪深 A 股目录中；"
                "请先执行证券基础信息补最新"
            )

    def _fetch_window(
        self,
        row: Any,
        actual: list[date],
        start_override: date | None = None,
    ) -> tuple[date, date]:
        """计算拉取窗口：从上市日/默认起点到退市日/最近交易日。"""
        start, end = self._expected_bounds(row, actual, start_override=start_override)
        if actual:
            start = max(_STOCK_FETCH_EPOCH, min(start, actual[0]))
        return start, end

    _DATASET_ALIASES = {
        "daily": "stock_daily_close",
        "close": "stock_daily_close",
        "stock_daily_close": "stock_daily_close",
        "basic": "stock_daily_basic",
        "daily_basic": "stock_daily_basic",
        "stock_daily_basic": "stock_daily_basic",
        "moneyflow": "stock_moneyflow",
        "stock_moneyflow": "stock_moneyflow",
    }
    _ALL_DATASETS = ("stock_daily_close", "stock_daily_basic", "stock_moneyflow")

    @staticmethod
    def _is_hs_stock_code(stock_code: str) -> bool:
        """判断 6 位代码是否为沪深 A 股（排除北交所与 B 股）。

        Args:
            stock_code: 6 位股票代码。

        Returns:
            True 表示沪深 A 股代码。
        """
        return stock_code.startswith(("00", "30", "60", "68"))

    def _normalize_datasets(self, datasets: list[str] | None) -> list[str]:
        """把 CLI/服务传入的数据集别名规范化为静态数据集键。

        Args:
            datasets: 数据集别名列表；None 表示全部个股数据集。

        Returns:
            去重后的数据集键列表。

        Raises:
            ValueError: 存在无法识别的数据集别名时抛出。
        """
        if not datasets:
            return list(self._ALL_DATASETS)
        normalized: list[str] = []
        for item in datasets:
            key = self._DATASET_ALIASES.get(str(item).strip().lower())
            if key is None:
                raise ValueError(f"未知个股数据集: {item}")
            normalized.append(key)
        return list(dict.fromkeys(normalized))

    def _fetch_full_rows(
        self,
        stock_code: str,
        start: date,
        end: date,
        datasets: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """拉取单只股票的 Tushare 日线、复权因子、每日指标与资金流向。

        Args:
            stock_code: 6 位股票代码。
            start: 起始日期（含）。
            end: 结束日期（含）。
            datasets: 需要抓取的数据集；None 表示全部。

        Returns:
            {数据集键: 行列表}；无数据的数据集返回空列表。
        """
        selected = self._normalize_datasets(datasets)
        if not self._is_hs_stock_code(stock_code):
            return {key: [] for key in selected}
        start_s = start.strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")
        result: dict[str, list[dict[str, Any]]] = {key: [] for key in selected}
        if "stock_daily_close" in selected:
            daily_rows = self._tushare_client.fetch_history_daily(stock_code, start_s, end_s)
            factor_map = {
                row["trade_date"]: row.get("adj_factor")
                for row in self._tushare_client.fetch_history_adj_factor(stock_code, start_s, end_s)
            }
            for row in daily_rows:
                row["adj_factor"] = factor_map.get(row["trade_date"])
            result["stock_daily_close"] = daily_rows
        if "stock_daily_basic" in selected:
            result["stock_daily_basic"] = self._tushare_client.fetch_history_daily_basic(
                stock_code, start_s, end_s
            )
        if "stock_moneyflow" in selected:
            result["stock_moneyflow"] = self._tushare_client.fetch_history_moneyflow(
                stock_code, start_s, end_s
            )
        return result

    def _upsert_dataset_rows(
        self,
        stock_code: str,
        dataset_key: str,
        rows: list[dict[str, Any]],
    ) -> int:
        """把单只股票的数据行幂等写入对应 Tushare 明细表。

        Args:
            stock_code: 6 位股票代码。
            dataset_key: 数据集键。
            rows: 已归一化的数据行。

        Returns:
            写入行数。
        """
        if not rows:
            return 0
        if dataset_key == "stock_daily_close":
            values = [
                {
                    "trade_date": row["trade_date"],
                    "stock_code": stock_code,
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "pre_close": row.get("pre_close"),
                    "change": row.get("change"),
                    "pct_chg": row.get("pct_chg"),
                    "vol": row.get("vol"),
                    "amount": row.get("amount"),
                    "ah_vol": row.get("ah_vol"),
                    "ah_amount": row.get("ah_amount"),
                    "adj_factor": row.get("adj_factor"),
                    "source": "tushare",
                    "ingested_at": utcnow(),
                }
                for row in rows
            ]
            return self._close_repo.bulk_upsert(values)
        if dataset_key == "stock_daily_basic":
            values = [
                {
                    **row,
                    "stock_code": stock_code,
                    "source": "tushare",
                    "ingested_at": utcnow(),
                }
                for row in rows
            ]
            return self._basic_repo.bulk_upsert(values)
        if dataset_key == "stock_moneyflow":
            values = [
                {
                    **row,
                    "stock_code": stock_code,
                    "source": "tushare",
                    "ingested_at": utcnow(),
                }
                for row in rows
            ]
            return self._moneyflow_repo.bulk_upsert(values)
        raise ValueError(f"未知个股数据集: {dataset_key}")

    def fill_stock(
        self,
        stock_code: str,
        *,
        in_session: bool = False,
        start_date: date | None = None,
        datasets: list[str] | None = None,
    ) -> dict[str, Any]:
        """补全单只股票 Tushare 明细到最近交易日。

        Args:
            stock_code: 6 位股票代码。
            in_session: 兼容旧 baostock 会话参数，Tushare 路径不使用。
            start_date: 起始日覆盖；None 表示按上市日/库内历史计算。
            datasets: 需要补全的数据集；None 表示日线、每日指标、资金流向全部。

        Returns:
            写入统计；质量结论不在此写入，统一由 DataManagementService 的
            检查步骤落到 data_health_snapshot。
        """
        del in_session  # 兼容旧调用签名；Tushare 客户端不使用 baostock 会话
        selected = self._normalize_datasets(datasets)
        self._ensure_stock_row(stock_code)
        row = self._universe_repo.find_by_code(stock_code)
        if row is None:
            raise RuntimeError(f"股票 {stock_code} 元数据初始化失败")
        actual = self._close_repo.find_trade_dates_by_code(stock_code)
        start, end = self._fetch_window(row, actual, start_override=start_date)
        fetched = (
            {key: [] for key in selected}
            if start > end
            else self._fetch_full_rows(stock_code, start, end, datasets=selected)
        )
        upserted: dict[str, int] = {}
        for dataset_key in selected:
            upserted[dataset_key] = self._upsert_dataset_rows(
                stock_code, dataset_key, fetched.get(dataset_key, [])
            )
        self._db.commit()
        return {
            "stock_code": stock_code,
            "datasets": selected,
            "fetched_rows": {key: len(fetched.get(key, [])) for key in selected},
            "upserted_rows": upserted,
            "upserted_total": sum(upserted.values()),
        }

    def rebuild_stock(
        self,
        stock_code: str,
        *,
        in_session: bool = False,
        start_date: date | None = None,
        datasets: list[str] | None = None,
    ) -> dict[str, Any]:
        """全量重拉单只股票：先完整拉取，成功后清空旧行并写回。

        Args:
            stock_code: 6 位股票代码。
            in_session: 兼容旧 baostock 会话参数，Tushare 路径不使用。
            start_date: 起始日覆盖。
            datasets: 需要重拉的数据集；None 表示全部个股数据集。

        Returns:
            删除/写入统计；质量结论由 DataManagementService 的检查步骤重算。
        """
        del in_session  # 兼容旧调用签名；Tushare 客户端不使用 baostock 会话
        selected = self._normalize_datasets(datasets)
        self._ensure_stock_row(stock_code)
        row = self._universe_repo.find_by_code(stock_code)
        if row is None:
            raise RuntimeError(f"股票 {stock_code} 元数据初始化失败")
        actual = self._close_repo.find_trade_dates_by_code(stock_code)
        start, end = self._fetch_window(row, actual, start_override=start_date)
        fetched = (
            {key: [] for key in selected}
            if start > end
            else self._fetch_full_rows(stock_code, start, end, datasets=selected)
        )
        if not any(fetched.get(key) for key in selected):
            raise RuntimeError(f"股票 {stock_code} 未获取到可替换的 Tushare 数据，已保留旧数据")
        deleted: dict[str, int] = {}
        for dataset_key in selected:
            if not fetched.get(dataset_key):
                # 单个数据集返回空时保留旧数据，避免上游短暂空响应清空历史
                continue
            if dataset_key == "stock_daily_close":
                deleted[dataset_key] = self._close_repo.delete_by_code(stock_code)
            elif dataset_key == "stock_daily_basic":
                deleted[dataset_key] = self._basic_repo.delete_by_code(stock_code)
            else:
                deleted[dataset_key] = self._moneyflow_repo.delete_by_code(stock_code)
            self._upsert_dataset_rows(stock_code, dataset_key, fetched.get(dataset_key, []))
        self._db.commit()
        return {
            "stock_code": stock_code,
            "datasets": selected,
            "deleted_rows": deleted,
            "upserted_rows": {key: len(fetched.get(key, [])) for key in selected},
            "upserted_total": sum(len(fetched.get(key, [])) for key in selected),
        }

    def sync_trade_date(
        self,
        trade_date: date,
        datasets: list[str] | None = None,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        """按交易日抓取全市场 Tushare 个股数据并分表写入。

        Args:
            trade_date: 交易日。
            datasets: 需要同步的数据集；None 表示全部个股数据集。
            commit: 是否在本次交易日写入后提交事务；批量修复可延后提交。

        Returns:
            {trade_date, datasets, records, errors}；单个数据集失败不中断其他数据集。
        """
        selected = self._normalize_datasets(datasets)
        if not self._tushare_client.is_configured():
            raise RuntimeError("未配置 TUSHARE_TOKEN，个股数据已切换为 Tushare 独占")
        records = {key: 0 for key in selected}
        errors: list[str] = []
        empty: list[str] = []
        if "stock_daily_close" in selected:
            try:
                daily_rows = self._tushare_client.fetch_daily_by_trade_date(trade_date)
                factor_map = {
                    row["stock_code"]: row.get("adj_factor")
                    for row in self._tushare_client.fetch_adj_factor_by_trade_date(trade_date)
                }
                values = [
                    {
                        "trade_date": trade_date,
                        "stock_code": row["stock_code"],
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row.get("close"),
                        "pre_close": row.get("pre_close"),
                        "change": row.get("change"),
                        "pct_chg": row.get("pct_chg"),
                        "vol": row.get("vol"),
                        "amount": row.get("amount"),
                        "ah_vol": row.get("ah_vol"),
                        "ah_amount": row.get("ah_amount"),
                        "adj_factor": factor_map.get(row["stock_code"]),
                        "source": "tushare",
                        "ingested_at": utcnow(),
                    }
                    for row in daily_rows
                    if self._is_hs_stock_code(row["stock_code"])
                ]
                if not values:
                    empty.append("stock_daily_close")
                records["stock_daily_close"] = self._close_repo.bulk_upsert(values)
            except Exception as exc:  # noqa: PERF203
                errors.append(f"stock_daily_close: {type(exc).__name__}: {exc}")
                logger.warning("个股日线同步失败 %s: %s", trade_date, exc)
        if "stock_daily_basic" in selected:
            try:
                rows = self._tushare_client.fetch_daily_basic_by_trade_date(trade_date)
                values = [
                    {
                        **row,
                        "trade_date": trade_date,
                        "source": "tushare",
                        "ingested_at": utcnow(),
                    }
                    for row in rows
                    if self._is_hs_stock_code(row["stock_code"])
                ]
                if not values:
                    empty.append("stock_daily_basic")
                records["stock_daily_basic"] = self._basic_repo.bulk_upsert(values)
            except Exception as exc:  # noqa: PERF203
                errors.append(f"stock_daily_basic: {type(exc).__name__}: {exc}")
                logger.warning("个股每日指标同步失败 %s: %s", trade_date, exc)
        if "stock_moneyflow" in selected:
            try:
                rows = self._tushare_client.fetch_moneyflow_by_trade_date(trade_date)
                values = [
                    {
                        **row,
                        "trade_date": trade_date,
                        "source": "tushare",
                        "ingested_at": utcnow(),
                    }
                    for row in rows
                    if self._is_hs_stock_code(row["stock_code"])
                ]
                if not values:
                    empty.append("stock_moneyflow")
                records["stock_moneyflow"] = self._moneyflow_repo.bulk_upsert(values)
            except Exception as exc:  # noqa: PERF203
                errors.append(f"stock_moneyflow: {type(exc).__name__}: {exc}")
                logger.warning("个股资金流向同步失败 %s: %s", trade_date, exc)
        if commit:
            self._db.commit()
        return {
            "trade_date": trade_date,
            "datasets": selected,
            "records": records,
            "empty_datasets": empty,
            "errors": errors,
        }

    def _dataset_latest_date(self, dataset_key: str) -> date | None:
        """查询指定个股数据集已写入的最新日期。"""
        if dataset_key == "stock_daily_close":
            return self._close_repo.latest_complete_date()
        if dataset_key == "stock_daily_basic":
            return self._basic_repo.latest_date()
        if dataset_key == "stock_moneyflow":
            return self._moneyflow_repo.latest_date()
        raise ValueError(f"未知个股数据集: {dataset_key}")

    def _dataset_has_date(self, dataset_key: str, trade_date: date) -> bool:
        """判断指定数据集在该交易日是否已有完整数据。"""
        if dataset_key == "stock_daily_close":
            return self._close_repo.count_trade_date(trade_date, require_open=True) > 0
        if dataset_key == "stock_daily_basic":
            return self._basic_repo.count_trade_date(trade_date) > 0
        if dataset_key == "stock_moneyflow":
            return self._moneyflow_repo.count_trade_date(trade_date) > 0
        raise ValueError(f"未知个股数据集: {dataset_key}")

    def dataset_trade_dates(
        self,
        dataset_key: str,
        start: date,
        end: date,
    ) -> list[date]:
        """返回单个数据集在区间内已覆盖的交易日。

        Args:
            dataset_key: 数据集键。
            start: 起始日期（含）。
            end: 结束日期（含）。

        Returns:
            去重升序交易日列表。
        """
        if dataset_key == "stock_daily_close":
            return self._close_repo.trading_dates_with_open(start, end)
        if dataset_key == "stock_daily_basic":
            return self._basic_repo.trading_dates(start, end)
        if dataset_key == "stock_moneyflow":
            return self._moneyflow_repo.trading_dates(start, end)
        raise ValueError(f"未知个股数据集: {dataset_key}")

    def missing_trade_dates(
        self,
        start: date,
        end: date,
        *,
        datasets: list[str] | None = None,
    ) -> dict[str, list[date]]:
        """计算各数据集相对交易日历的缺失日期。

        Args:
            start: 起始日期（含）。
            end: 结束日期（含）。
            datasets: 需要检查的数据集；None 表示全部个股数据集。

        Returns:
            {数据集键: 缺失交易日列表}。
        """
        selected = self._normalize_datasets(datasets)
        trading_days = set(self._trading_days(start, end))
        return {
            key: sorted(trading_days - set(self.dataset_trade_dates(key, start, end)))
            for key in selected
        }

    def sync_range(
        self,
        start: date,
        end: date,
        *,
        datasets: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """按交易日区间回填 Tushare 个股数据（幂等、可续跑）。

        Args:
            start: 起始日期（含）。
            end: 结束日期（含）。
            datasets: 需要回填的数据集；None 表示全部个股数据集。
            force: 为 True 时忽略已完成判断，重新抓取所有交易日。

        Returns:
            {start, end, trading_days, synced_dates, skipped_dates, records, errors}。

        Raises:
            ValueError: start > end 时抛出。
        """
        if start > end:
            raise ValueError("起始日期不能晚于结束日期")
        selected = self._normalize_datasets(datasets)
        trading_days = self._trading_days(start, end)
        records = {key: 0 for key in selected}
        errors: list[str] = []
        upstream_empty = {key: set() for key in selected}
        synced_dates = 0
        skipped_dates = 0
        for index, trade_date in enumerate(trading_days, start=1):
            missing = [
                key for key in selected if force or not self._dataset_has_date(key, trade_date)
            ]
            if not missing:
                skipped_dates += 1
                continue
            result = self.sync_trade_date(trade_date, datasets=missing)
            synced_dates += 1
            for key, count in result["records"].items():
                records[key] += int(count)
            for key in result.get("empty_datasets", []):
                upstream_empty.setdefault(key, set()).add(trade_date)
            errors.extend(result["errors"])
            if index % 50 == 0:
                logger.info(
                    "Tushare 个股回填进度: %s/%s 日期=%s",
                    index,
                    len(trading_days),
                    trade_date,
                )
        return {
            "start": start,
            "end": end,
            "trading_days": len(trading_days),
            "synced_dates": synced_dates,
            "skipped_dates": skipped_dates,
            "records": records,
            "upstream_empty": {
                key: sorted(values) for key, values in upstream_empty.items()
            },
            "errors": errors[:50],
        }

    def sync_missing_recent(
        self,
        *,
        lookback_days: int = 15,
        datasets: list[str] | None = None,
    ) -> dict[str, Any]:
        """补齐最近一段时间的 Tushare 个股缺口，供每日调度使用。

        Args:
            lookback_days: 每个数据集最多向前回看的自然日数，避免调度任务全量回填。
            datasets: 需要补缺的数据集；None 表示全部个股数据集。

        Returns:
            {expected_date, datasets, records, errors, results}。
        """
        selected = self._normalize_datasets(datasets)
        expected = self._latest_trading_day()
        floor = max(_STOCK_FETCH_EPOCH, expected - timedelta(days=lookback_days))
        records = {key: 0 for key in selected}
        errors: list[str] = []
        results: dict[str, Any] = {}
        for key in selected:
            latest = self._dataset_latest_date(key)
            start = floor if latest is None else max(floor, latest + timedelta(days=1))
            if start > expected:
                results[key] = {"skipped": True, "start": start, "end": expected}
                continue
            result = self.sync_range(start, expected, datasets=[key])
            results[key] = result
            records[key] += int(result["records"].get(key) or 0)
            errors.extend(result["errors"])
        return {
            "expected_date": expected,
            "datasets": selected,
            "records": records,
            "errors": errors[:50],
            "results": results,
        }

    def prune_before(
        self,
        before: date,
        *,
        dry_run: bool = True,
        batch_size: int = 50000,
    ) -> dict[str, Any]:
        """删除指定日期之前的个股日线（分批提交，默认仅预演）。

        Args:
            before: 截止日期（不含）。
            dry_run: 为 True 时只统计不删除。
            batch_size: 单批删除行数。

        Returns:
            {before, matched, deleted, dry_run}。
        """
        matched = self._close_repo.count_before(before)
        if dry_run:
            return {"before": before, "matched": matched, "deleted": 0, "dry_run": True}
        deleted = 0
        while True:
            count = self._close_repo.delete_before_batch(before, batch_size)
            self._db.commit()
            deleted += count
            if count == 0:
                break
        return {"before": before, "matched": matched, "deleted": deleted, "dry_run": False}

    def prune_non_tushare(
        self,
        start: date,
        *,
        dry_run: bool = True,
        batch_size: int = 50000,
    ) -> dict[str, Any]:
        """删除指定日期之后非 Tushare 或非沪深 A 股的个股日线（默认仅预演）。

        Args:
            start: 起始日期（含）。
            dry_run: 为 True 时只统计不删除。
            batch_size: 单批删除行数。

        Returns:
            {start, matched, deleted, dry_run}。
        """
        matched = self._close_repo.count_non_tushare(start)
        if dry_run:
            return {"start": start, "matched": matched, "deleted": 0, "dry_run": True}
        deleted = 0
        while True:
            count = self._close_repo.delete_non_tushare_batch(start, batch_size)
            self._db.commit()
            deleted += count
            if count == 0:
                break
        return {"start": start, "matched": matched, "deleted": deleted, "dry_run": False}

    def bulk_fill(
        self,
        *,
        codes: list[str] | None = None,
        start_date: date | None = None,
        only_missing: bool = False,
        rebuild: bool = False,
        datasets: list[str] | None = None,
    ) -> dict[str, Any]:
        """批量补全/重拉股票 Tushare 明细（仅 CLI 使用）。

        Args:
            codes: 股票代码列表；None 表示 stock_universe 全部代码。
            start_date: 起始日覆盖。
            only_missing: 仅处理健康快照缺失或异常的分区。
            rebuild: 为 True 时走全量重拉。
            datasets: 需要处理的数据集；None 表示全部个股数据集。

        Returns:
            {codes, ok, records, rebuild, datasets, errors}。
        """
        selected = self._normalize_datasets(datasets)
        target_codes = self._resolve_codes(codes)
        if only_missing and not codes:
            health = self._health_repo.find_by_dataset_and_partitions(
                "stock_daily_close", target_codes
            )
            target_codes = [
                code
                for code in target_codes
                if health.get(code) is None or health[code].health_status != "healthy"
            ]
        records = 0
        errors: list[str] = []
        for code in target_codes:
            try:
                if rebuild:
                    result = self.rebuild_stock(code, start_date=start_date, datasets=selected)
                else:
                    result = self.fill_stock(code, start_date=start_date, datasets=selected)
                records += int(result.get("upserted_total") or 0)
            except Exception as exc:
                errors.append(f"{code}: {type(exc).__name__}: {exc}")
                logger.warning("个股 %s 批量补全失败: %s", code, exc)
        return {
            "codes": len(target_codes),
            "ok": len(target_codes) - len(errors),
            "records": records,
            "rebuild": rebuild,
            "datasets": selected,
            "errors": errors,
        }
