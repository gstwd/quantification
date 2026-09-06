"""申万行业轮动数据服务：目录同步、日线/成分/个股收盘摄取。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.industry.constants import (
    SW_CLASSIFICATION_EPOCH,
    SW_CLASSIFICATION_PREFIX_TO_L1,
    SW_CLASSIFICATION_REMAP_SINCE,
    SW_EXCLUDED_INDUSTRY_CODES,
    SW_L1_NAMES,
)
from quant_etf_api.infra.clients.stock_close_client import StockCloseClient
from quant_etf_api.infra.clients.sw_industry_client import SwIndustryClient
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.industry import IndustryUniverseModel
from quant_etf_api.infra.db.repositories.industry import (
    IndustryDailyBarRepository,
    IndustryMembershipEventRepository,
    IndustryUniverseRepository,
    StockDailyCloseRepository,
)

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
        """全量回填行业指数日线（从申万官网全历史）。"""
        codes = self._resolve_codes(industry_codes)
        total = 0
        errors: list[str] = []
        for code in codes:
            try:
                rows = self._sw_client.fetch_daily(code)
                total += self._upsert_bars(code, rows)
            except Exception as e:
                logger.warning("行业 %s 日线全量回填失败: %s", code, e)
                errors.append(f"{code}: {type(e).__name__}")
        self._db.commit()
        return {"codes": len(codes), "records": total, "errors": errors}

    def refresh_industry_bars_incremental(self, industry_codes: list[str] | None = None) -> int:
        """增量更新行业指数日线（从库内最新日期补到最新）。"""
        codes = self._resolve_codes(industry_codes)
        total = 0
        for code in codes:
            try:
                latest = self._bar_repo.latest_trade_date(code)
                if latest is None:
                    rows = self._sw_client.fetch_daily(code)
                else:
                    start = (latest - timedelta(days=_INCREMENTAL_BUFFER_DAYS)).strftime("%Y%m%d")
                    rows = self._sw_client.fetch_daily(code, start_date=start)
                total += self._upsert_bars(code, rows)
            except Exception as e:
                logger.warning("行业 %s 日线增量更新失败: %s", code, e)
        self._db.commit()
        return total

    def _upsert_bars(self, code: str, rows: list[dict[str, Any]]) -> int:
        """将行业日线行幂等写入 industry_daily_bar。"""
        if not rows:
            return 0
        values = []
        for row in rows:
            if row.get("close_price") is None:
                continue
            values.append(
                {
                    "trade_date": row["trade_date"],
                    "industry_code": code,
                    "open_price": row.get("open_price"),
                    "high_price": row.get("high_price"),
                    "low_price": row.get("low_price"),
                    "close_price": row.get("close_price"),
                    "volume": row.get("volume"),
                    "turnover": row.get("turnover"),
                    "source": row.get("source") or "akshare_sw",
                    "ingested_at": utcnow(),
                }
            )
        return self._bar_repo.bulk_upsert(values)

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
        """回填全部成分股历史收盘价（baostock，回退 AkShare）。"""
        codes = stock_codes or sorted(
            {event.stock_code for event in self._membership_repo.find_all_events()}
        )
        if not codes:
            return {"codes": 0, "records": 0, "errors": []}
        total = 0
        errors: list[str] = []
        end = date.today().strftime("%Y%m%d")
        for i, code in enumerate(codes, start=1):
            try:
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
        """用当日全 A 快照更新指定交易日收盘价。"""
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

    def run_daily_ingest(self, target_date: date | None = None) -> date | None:
        """执行行业轮动子系统日频数据摄取。

        Args:
            target_date: 期望补数的交易日；None 时以行业日线最新日期为准。

        Returns:
            实际补齐数据的交易日；无数据时返回 None。
        """
        self.refresh_industry_bars_incremental()
        latest = self._bar_repo.latest_trade_date()
        if latest is None:
            return None
        try:
            self.refresh_membership()
        except Exception:
            # 成分刷新失败不阻塞当日行业日线与收盘更新，后续可手动重跑
            logger.warning(
                "行业成分刷新失败，跳过（可稍后执行 backfill-membership）", exc_info=True
            )
        try:
            self.refresh_stock_close_snapshot(target_date or latest)
        except Exception:
            logger.warning(
                "个股收盘快照刷新失败，跳过（可稍后执行 backfill-stock-close）", exc_info=True
            )
        return latest

    def _resolve_codes(self, codes: list[str] | None) -> list[str]:
        """解析行业代码列表；为空时使用全部启用行业。"""
        if codes:
            return sorted(set(codes))
        return self._universe_repo.find_active_codes()
