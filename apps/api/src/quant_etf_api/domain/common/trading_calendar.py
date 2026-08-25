"""交易日历抽象协议（纯领域层）。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class TradingCalendarLike(Protocol):
    """交易日历协议，供领域规则依赖的最小接口。

    领域层不直接依赖 infra 的 TradingCalendar 实现，
    而是依赖此协议，便于独立测试与替换实现。
    """

    def is_trading_day(self, day: date) -> bool:
        """判断指定日期是否为 A 股交易日。

        Args:
            day: 待判断的日期。

        Returns:
            True 表示交易日。
        """
        ...

    def latest_trading_day(self, day: date) -> date:
        """返回不晚于指定日期的最近交易日。

        Args:
            day: 参考日期。

        Returns:
            最近交易日。
        """
        ...


class WeekendFallbackCalendar:
    """纯周末判断的交易日历兜底实现。

    不访问任何外部数据源，仅在无 infra 实现可注入时使用。
    """

    def is_trading_day(self, day: date) -> bool:
        """按周末规则判断是否为交易日。

        Args:
            day: 待判断的日期。

        Returns:
            True 表示周一至周五。
        """
        return day.weekday() < 5

    def latest_trading_day(self, day: date) -> date:
        """回退到最近的周一至周五。

        Args:
            day: 参考日期。

        Returns:
            最近的工作日。
        """
        cursor = day
        while cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
        return cursor
