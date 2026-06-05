"""调仓模块：控制调仓时间。

支持 daily / weekly / monthly 频率。
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from quant_etf_api.engine.config import RebalanceConfig


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
    """默认调仓调度器。"""

    def should_rebalance(
        self,
        config: RebalanceConfig,
        current_date: date,
        last_rebalance_date: date | None = None,
    ) -> bool:
        """判断是否应该调仓。"""
        if config.frequency == "daily":
            return True

        if config.frequency == "weekly":
            target_day = config.day_of_week if config.day_of_week is not None else 4
            return current_date.weekday() == target_day

        if config.frequency == "monthly":
            target_day = config.day_of_month if config.day_of_month is not None else 1
            return current_date.day == target_day

        return True
