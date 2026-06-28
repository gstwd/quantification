"""调仓模块：控制调仓时间。

支持 daily / weekly / monthly 频率。
引入交易日历对齐：调仓日如遇非交易日，自动顺延至下一交易日。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Protocol

from quant_etf_api.engine.config import RebalanceConfig

logger = logging.getLogger(__name__)


class RebalanceScheduler(Protocol):
    """调仓调度器协议。"""

    def should_rebalance(
        self,
        config: RebalanceConfig,
        current_date: date,
        last_rebalance_date: date | None = None,
    ) -> bool:
        """判断是否应该调仓。

        Args:
            config: 调仓配置。
            current_date: 当前日期。
            last_rebalance_date: 上次调仓日期。

        Returns:
            是否应该调仓。
        """
        ...


class DefaultRebalanceScheduler:
    """默认调仓调度器。

    支持交易日历对齐：当调仓目标日为非交易日时，
    自动顺延至下一交易日，避免因节假日跳过整月/整周的调仓。
    """

    def __init__(self, trading_calendar=None) -> None:
        """初始化调仓调度器。

        Args:
            trading_calendar: TradingCalendar 实例，未提供时降级为周末判断。
        """
        from quant_etf_api.infra.trading_calendar import TradingCalendar

        self._cal = trading_calendar or TradingCalendar()

    def should_rebalance(
        self,
        config: RebalanceConfig,
        current_date: date,
        last_rebalance_date: date | None = None,
    ) -> bool:
        """判断是否应该调仓。

        交易日历对齐逻辑：
        - daily：永远在当前交易日调仓。
        - weekly：若目标 weekday 非交易日，顺延至下一交易日；
          若当前已是该周最后一个交易日且之后无交易日 → 在当前日调仓。
        - monthly：若目标 day_of_month 非交易日，顺延至下一交易日；
          若当月剩余日期无交易日 → 在当前日调仓。
        """
        if config.frequency == "daily":
            return True

        if config.frequency == "weekly":
            target_day = config.day_of_week if config.day_of_week is not None else 4
            # 如果当前日正好是目标 weekday，直接调仓
            if current_date.weekday() == target_day:
                return True
            # 如果当前日之后的最近交易日仍在同一周且 weekday 匹配 → 顺延
            return self._is_nearest_in_window(current_date, target_day, "week")

        if config.frequency == "monthly":
            target_day = config.day_of_month if config.day_of_month is not None else 1
            # 如果当前日正好是目标日，直接调仓
            if current_date.day == target_day:
                return True
            # 如果当前日是该月剩余日中的第一个可交易日 → 顺延
            return self._is_nearest_in_window(current_date, target_day, "month")

        return True

    def _is_nearest_in_window(self, current_date: date, target: int, window: str) -> bool:
        """判断当前日是否为目标窗口内的最近交易日。

        算法：从目标日向后查找，第一个交易日即为调仓日。
        如果该交易日就是 current_date，则调仓。

        Args:
            current_date: 当前日期。
            target: 目标 weekday(0-6) 或 day_of_month(1-31)。
            window: "week" 或 "month"。

        Returns:
            是否应在当前日调仓。
        """
        try:
            if window == "week":
                # 查找本周内 >= 目标 weekday 的最近交易日
                days_since_target = current_date.weekday() - target
                if days_since_target < 0:
                    return False  # 还没到目标 weekday
                # 从目标 weekday 开始，找到的第一个交易日
                target_date = current_date - timedelta(days=days_since_target)
            else:
                # 查找本月内 >= 目标 day_of_month 的最近交易日
                if current_date.day < target:
                    return False  # 还没到目标日
                target_date = current_date.replace(day=target)

            # 从目标日起向前查找，检查 current_date 是否为第一个交易日
            check = target_date
            max_days = 10  # 最多查找 10 天
            for _ in range(max_days):
                if self._cal.is_trading_day(check):
                    return check == current_date
                check += timedelta(days=1)
                # 如果跨出当前周/月，停止
                if window == "week" and check.weekday() == 0:
                    break
                if window == "month" and check.day == 1:
                    break

            # 降级：如果窗口内无交易日，当前日就是最后的选项
            logger.warning(
                "%s 调仓窗口内未找到交易日: target=%s current=%s, 降级使用当前日",
                window,
                target,
                current_date,
            )
            return True
        except Exception:
            logger.warning("交易日历对齐失败，降级为原始判断", exc_info=True)
            # 降级：不做交易日历对齐
            if window == "week":
                return current_date.weekday() == target
            return current_date.day == target
