"""系统时间工具，统一 UTC 时间戳和中国市场业务日期。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")


def utcnow() -> datetime:
    """返回当前 UTC 时间的 naive datetime，供历史 naive 时间戳列使用。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_aware() -> datetime:
    """返回当前 UTC 时间的 aware datetime，供 timestamptz 列使用（B5）。

    队列与回测的 started_at/finished_at 等列已迁移为 timestamptz，
    写入时携带时区信息可避免"应用写 naive、SQL 侧 now() 带时区"混用
    导致的 8 小时误差。
    """
    return datetime.now(timezone.utc)


def now_cn() -> datetime:
    """返回当前中国标准时间的 aware datetime，供调度和展示边界使用。"""
    return datetime.now(CHINA_TZ)


def today_cn() -> date:
    """返回中国标准时间下的当前业务日期。"""
    return now_cn().date()


def shift_date_cn(value: date, days: int) -> date:
    """按自然日偏移业务日期。"""
    return value + timedelta(days=days)
