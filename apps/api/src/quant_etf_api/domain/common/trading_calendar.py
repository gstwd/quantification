"""交易日历抽象协议（纯领域层）。

领域层只依赖最小接口，不依赖任何 infra 实现，便于独立测试与替换实现。
系统对交易日历采取**严格口径**：只接受真实日历（上游数据源 Tushare/AkShare
或本地 trading_calendar 表快照），不接受任何"按星期猜交易日"的近似实现——
近似日历会在长假后错位调仓日，导致同一配置出现两套不可复现的回测结果（C1）。
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable


class TradingCalendarUnavailableError(RuntimeError):
    """交易日历不可用：上游数据源与本地 trading_calendar 表均无法提供有效日历。

    该异常表示"无法给出正确的交易日答案"，调用方必须显式处理（上抛落失败、
    拒绝执行、或返回 503），不得退化为近似结果。
    """


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

        Raises:
            TradingCalendarUnavailableError: 日历不可用时抛出。
        """
        ...

    def latest_trading_day(self, day: date) -> date:
        """返回不晚于指定日期的最近交易日。

        Args:
            day: 参考日期。

        Returns:
            最近交易日。

        Raises:
            TradingCalendarUnavailableError: 日历不可用时抛出。
        """
        ...
