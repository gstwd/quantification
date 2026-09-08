"""指数成分数据服务（当前快照刷新与 PIT 取样回填）。"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.infra.time import today_cn

from quant_etf_api.infra.clients.index_member_client import (
    AkShareIndexMemberClient,
    BaostockIndexMemberClient,
)
from quant_etf_api.infra.clients.tushare_market import TushareIndexMemberClient
from quant_etf_api.infra.db.repositories.index_member import IndexMemberEventRepository
from quant_etf_api.infra.trading_calendar import TradingCalendar

logger = logging.getLogger(__name__)

# 空响应重试间隔（秒），区分 Tushare 偶发空返回与真实无数据
_EMPTY_RETRY_SLEEP = 0.5


class IndexMembershipDataService:
    """指数成分事件的摄取与状态查询。"""

    def __init__(self, db: Session) -> None:
        """初始化指数成分服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._repo = IndexMemberEventRepository(db)
        self._akshare = AkShareIndexMemberClient()
        self._baostock = BaostockIndexMemberClient()
        self._tushare = TushareIndexMemberClient()

    def refresh_current_snapshots(self, index_codes: list[str]) -> dict[str, Any]:
        """拉取并替换指数当前成分/权重快照（Tushare 优先，幂等）。"""
        snapshot_date = today_cn()
        cal = TradingCalendar()
        if not cal.is_trading_day(snapshot_date):
            snapshot_date = cal.latest_trading_day(snapshot_date)
        items: dict[str, Any] = {}
        errors: list[str] = []
        for index_code in sorted(index_codes):
            source = "akshare_csindex"
            rows = []
            if self._tushare.is_configured():
                try:
                    rows = self._tushare.fetch_weight_snapshot(index_code, snapshot_date)
                    source = "tushare"
                except Exception as exc:
                    logger.warning(
                        "指数 %s tushare 成分拉取失败，回退 AkShare: %s",
                        index_code,
                        exc,
                    )
            if not rows:
                rows = self._akshare.fetch_current_snapshot(index_code)
                source = "akshare_csindex"
            if not rows:
                errors.append(f"{index_code}: 免费源未返回成分数据")
                items[index_code] = 0
                continue
            normalized = [
                {
                    "index_code": index_code,
                    "stock_code": row["stock_code"],
                    "start_date": snapshot_date,
                    "end_date": None,
                    "weight": row.get("weight"),
                    "source": source,
                    "snapshot_type": "current_snapshot",
                }
                for row in rows
            ]
            count = self._repo.replace_current_snapshot(index_code, normalized)
            items[index_code] = count
            logger.info("指数当前成分刷新完成: %s rows=%s date=%s", index_code, count, snapshot_date)
        self._db.commit()
        return {"snapshot_date": snapshot_date.isoformat(), "items": items, "errors": errors}

    def backfill_pit(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, Any]:
        """按每交易月月末取样回填 PIT 历史成分（Tushare 优先，baostock 兜底）。

        从最新月份向前回填：单月空返回先重试一次（Tushare 偶发空响应），
        连续两个月均无数据才视为已到达该指数的历史数据起点并停止，避免把
        瞬时空响应误判为“指数尚未发布”而截断真实历史。
        """
        cal = TradingCalendar()
        samples: list[date] = []
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            # 计算当月最后一天：12 月需跨年到次年 1 月 1 日再回退一天
            next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            last_day = next_month - timedelta(days=1)
            last_day = min(last_day, end)
            trade_day = cal.latest_trading_day(last_day) if last_day >= start else None
            if trade_day is not None and trade_day >= start:
                samples.append(trade_day)
            month += 1
            if month > 12:
                year += 1
                month = 1
        items: dict[str, Any] = {}
        errors: list[str] = []
        for index_code in sorted(index_codes):
            total = 0
            empty_streak = 0
            for i, sample_date in reversed(list(enumerate(samples))):
                source = "baostock"
                members = []
                tushare_failed = False
                if self._tushare.is_configured():
                    for attempt in range(2):
                        try:
                            fetched = self._tushare.fetch_weight_snapshot(
                                index_code, sample_date
                            )
                        except Exception as exc:
                            tushare_failed = True
                            logger.warning(
                                "指数 %s@%s tushare 成分拉取失败（第 %d 次），"
                                "回退 baostock: %s",
                                index_code,
                                sample_date,
                                attempt + 1,
                                exc,
                            )
                            fetched = []
                        if fetched:
                            members = fetched
                            source = "tushare"
                            break
                        # 空返回先短暂等待再重试一次，区分瞬时空响应与真实无数据
                        time.sleep(_EMPTY_RETRY_SLEEP)
                if not members:
                    members = self._baostock.fetch_pit(index_code, sample_date)
                    source = "baostock"
                if not members:
                    if tushare_failed:
                        errors.append(
                            f"{index_code}@{sample_date}: tushare/baostock "
                            "均未返回成分"
                        )
                        empty_streak = 0
                        continue
                    empty_streak += 1
                    if empty_streak < 2:
                        continue
                    logger.info(
                        "指数 %s 自 %s 起无更早成分快照，停止向前回填",
                        index_code,
                        sample_date,
                    )
                    break
                empty_streak = 0
                end_date = samples[i + 1] - timedelta(days=1) if (
                    i + 1 < len(samples)
                ) else None
                rows = [
                    {
                        "index_code": index_code,
                        "stock_code": row["stock_code"],
                        "start_date": sample_date,
                        "end_date": end_date,
                        "weight": row.get("weight"),
                        "source": source,
                        "snapshot_type": "pit",
                    }
                    for row in members
                ]
                total += self._repo.bulk_upsert_pit(rows)
            items[index_code] = total
            logger.info(
                "指数 PIT 成分回填完成: %s rows=%d",
                index_code,
                total,
            )
        self._db.commit()
        return {"start": start.isoformat(), "end": end.isoformat(), "items": items, "errors": errors}

    def status(self, index_codes: list[str]) -> list[dict[str, Any]]:
        """返回各指数成分事件覆盖状态。"""
        result: list[dict[str, Any]] = []
        for index_code in sorted(index_codes):
            events = self._repo.find_events(index_code)
            starts = sorted({e.start_date for e in events})
            result.append(
                {
                    "index_code": index_code,
                    "event_count": len(events),
                    "first_start_date": starts[0].isoformat() if starts else None,
                    "latest_start_date": starts[-1].isoformat() if starts else None,
                    "pit_events": sum(1 for e in events if e.snapshot_type == "pit"),
                    "current_snapshot_events": sum(
                        1 for e in events if e.snapshot_type == "current_snapshot"
                    ),
                }
            )
        return result
