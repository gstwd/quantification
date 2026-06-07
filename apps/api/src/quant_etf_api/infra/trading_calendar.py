"""A 股交易日历模块。

从 AkShare 获取 A 股历史交易日历，提供交易日判断、最近交易日查询等功能。
首次调用时从 AkShare API 加载日历数据并缓存在内存中，TTL=1 天。
API 不可用时降级为周末判断。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 缓存 TTL：1 天
_CACHE_TTL = timedelta(days=1)

# 进程级缓存
_cache: dict[str, Any] = {
    "trading_days": None,  # set[date] | None
    "loaded_at": None,  # datetime | None
}


def _is_weekend(d: date) -> bool:
    """判断是否为周末（降级方案）。"""
    return d.weekday() >= 5


def _load_from_akshare() -> set[date] | None:
    """从 AkShare 加载 A 股交易日历。

    Returns:
        交易日集合，加载失败时返回 None。
    """
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            logger.warning("AkShare 交易日历返回空数据，降级为周末判断")
            return None

        # trade_date 列格式为 'YYYY-MM-DD'
        trading_days: set[date] = set()
        for _, row in df.iterrows():
            try:
                td = datetime.strptime(str(row["trade_date"]), "%Y-%m-%d").date()
                trading_days.add(td)
            except (ValueError, KeyError):
                continue

        if not trading_days:
            logger.warning("AkShare 交易日历解析后为空，降级为周末判断")
            return None

        logger.info("从 AkShare 加载交易日历成功，共 %d 个交易日", len(trading_days))
        return trading_days
    except Exception:
        logger.warning("AkShare 交易日历加载失败，降级为周末判断", exc_info=True)
        return None


def _get_cached_trading_days() -> set[date] | None:
    """获取缓存的交易日集合，缓存过期时重新加载。

    Returns:
        交易日集合，加载失败时返回 None。
    """
    now = datetime.now(timezone.utc)
    cached = _cache["trading_days"]
    loaded_at = _cache["loaded_at"]

    if cached is not None and loaded_at is not None:
        if now - loaded_at < _CACHE_TTL:
            return cached

    # 缓存过期或未初始化，重新加载
    trading_days = _load_from_akshare()
    _cache["trading_days"] = trading_days
    _cache["loaded_at"] = now
    return trading_days


def _refresh_cache() -> set[date] | None:
    """强制刷新缓存。"""
    _cache["trading_days"] = None
    _cache["loaded_at"] = None
    return _get_cached_trading_days()


class TradingCalendar:
    """A 股交易日历。

    提供交易日判断、最近交易日查询、交易日区间生成等功能。
    数据来源：AkShare tool_trade_date_hist_sina()，缓存 TTL=1 天。
    API 不可用时自动降级为周末判断。

    用法::

        cal = TradingCalendar()
        cal.is_trading_day(date.today())
        cal.latest_trading_day()
    """

    def is_trading_day(self, target: date) -> bool:
        """判断指定日期是否为 A 股交易日。

        Args:
            target: 目标日期。

        Returns:
            True 表示是交易日。
        """
        trading_days = _get_cached_trading_days()
        if trading_days is not None:
            return target in trading_days
        # 降级：排除周末
        return not _is_weekend(target)

    def latest_trading_day(self, reference: date | None = None) -> date:
        """获取指定日期（含）之前最近的交易日。

        Args:
            reference: 参考日期，默认今天。

        Returns:
            最近的交易日日期。
        """
        target = reference or date.today()
        trading_days = _get_cached_trading_days()

        if trading_days is not None:
            # 从参考日期向前查找
            for i in range(30):  # 最多回溯 30 天（覆盖最长节假日）
                d = target - timedelta(days=i)
                if d in trading_days:
                    return d
            logger.warning("交易日历回溯 30 天未找到交易日，降级为周末判断")
            # 降级
            while _is_weekend(target):
                target = target - timedelta(days=1)
            return target

        # 降级：排除周末
        while _is_weekend(target):
            target = target - timedelta(days=1)
        return target

    def next_trading_day(self, reference: date | None = None) -> date:
        """获取指定日期（不含）之后最近的下一个交易日。

        Args:
            reference: 参考日期，默认今天。

        Returns:
            下一个交易日日期。
        """
        target = (reference or date.today()) + timedelta(days=1)
        trading_days = _get_cached_trading_days()

        if trading_days is not None:
            for i in range(30):
                d = target + timedelta(days=i)
                if d in trading_days:
                    return d
            logger.warning("交易日历向前 30 天未找到交易日，降级为周末判断")
            while _is_weekend(target):
                target = target + timedelta(days=1)
            return target

        while _is_weekend(target):
            target = target + timedelta(days=1)
        return target

    def trading_days_between(self, start: date, end: date) -> list[date]:
        """获取两个日期之间（含起止）的所有交易日。

        Args:
            start: 起始日期。
            end: 结束日期。

        Returns:
            交易日列表，按日期升序排列。
        """
        trading_days = _get_cached_trading_days()
        if trading_days is not None:
            result = sorted(d for d in trading_days if start <= d <= end)
            return result

        # 降级：生成日期范围后过滤周末
        result: list[date] = []
        current = start
        while current <= end:
            if not _is_weekend(current):
                result.append(current)
            current += timedelta(days=1)
        return result

    def refresh(self) -> None:
        """强制刷新交易日历缓存。"""
        _refresh_cache()

    def get_trading_days_set(self) -> set[date] | None:
        """获取原始交易日集合（用于需要快速查找的场景）。

        Returns:
            交易日集合，加载失败时返回 None。
        """
        return _get_cached_trading_days()
