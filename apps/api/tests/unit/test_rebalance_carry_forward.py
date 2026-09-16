"""调仓顺延（目标日休市）与调仓配置校验单元测试。

覆盖：
- 周度目标日休市：同周顺延到下一个开市日、跨周顺延到下周一，且同一周只触发一次；
- 月度目标日休市：顺延到当月或下月的第一个交易日；目标日超出当月天数时按当月
  最后一日处理（历史缺陷：整月不调仓）；
- 传入非交易日一律不调仓；日历异常仍上抛（C1 不允许降级为按星期近似）；
- ``RebalanceConfig`` 的枚举/范围强校验。
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from quant_etf_api.domain.common.trading_calendar import TradingCalendarUnavailableError
from quant_etf_api.domain.strategies.rebalance import DefaultRebalanceScheduler
from quant_etf_api.engine.config import RebalanceConfig


class _Calendar:
    """固定休市日集合的日历替身（周末自动休市）。"""

    def __init__(self, holidays: set[date]) -> None:
        """初始化替身日历。

        Args:
            holidays: 额外的休市日集合（周末已自动休市）。
        """
        self._holidays = holidays

    def is_trading_day(self, day: date) -> bool:
        """按周末与给定休市日判定交易日。"""
        return day.weekday() < 5 and day not in self._holidays


class _BrokenCalendar:
    """总是抛日历不可用异常的替身。"""

    def is_trading_day(self, day: date) -> bool:
        """始终抛错，模拟日历不可用。"""
        raise TradingCalendarUnavailableError("交易日历不可用：测试桩")


class TestWeeklyCarryForward:
    """周度调仓的目标日休市顺延。"""

    def test_carries_forward_within_same_week(self) -> None:
        """目标周三休市 → 顺延到同周周四，且该周只触发一次。"""
        # 2025-05-05(一) ~ 05-09(五)，05-07(三) 休市
        scheduler = DefaultRebalanceScheduler(_Calendar({date(2025, 5, 7)}))
        cfg = RebalanceConfig(frequency="weekly", day_of_week=2)

        assert scheduler.should_rebalance(cfg, date(2025, 5, 5)) is False
        assert scheduler.should_rebalance(cfg, date(2025, 5, 6)) is False
        assert scheduler.should_rebalance(cfg, date(2025, 5, 8)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 5, 9)) is False

    def test_carries_forward_across_week_boundary(self) -> None:
        """目标周五休市 → 跨周顺延到下周一（历史缺陷：整周不调仓）。"""
        # 2025-05-09(五) 休市，下一个交易日为 05-12(一)
        scheduler = DefaultRebalanceScheduler(_Calendar({date(2025, 5, 9)}))
        cfg = RebalanceConfig(frequency="weekly", day_of_week=4)

        assert scheduler.should_rebalance(cfg, date(2025, 5, 8)) is False
        assert scheduler.should_rebalance(cfg, date(2025, 5, 12)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 5, 13)) is False

    def test_target_day_trading_triggers_once(self) -> None:
        """目标日为交易日时当天触发，之后同周不再触发。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        cfg = RebalanceConfig(frequency="weekly", day_of_week=4)

        assert scheduler.should_rebalance(cfg, date(2025, 5, 9)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 5, 8)) is False

    def test_default_target_is_friday(self) -> None:
        """未指定 day_of_week 时默认周五。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        cfg = RebalanceConfig(frequency="weekly")

        assert scheduler.should_rebalance(cfg, date(2025, 5, 9)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 5, 8)) is False


class TestMonthlyCarryForward:
    """月度调仓的目标日休市顺延与月末 clamp。"""

    def test_carries_forward_across_month_boundary(self) -> None:
        """目标 1/28 休市且跨月 → 顺延到 2 月首个交易日（历史缺陷：整月不调仓）。"""
        holidays = {
            date(2025, 1, 28),
            date(2025, 1, 29),
            date(2025, 1, 30),
            date(2025, 1, 31),
            date(2025, 2, 3),
            date(2025, 2, 4),
        }
        scheduler = DefaultRebalanceScheduler(_Calendar(holidays))
        cfg = RebalanceConfig(frequency="monthly", day_of_month=28)

        # 1/27（周一）未到目标日，且上月（12/28）之后已有交易日 → 不调仓
        assert scheduler.should_rebalance(cfg, date(2025, 1, 27)) is False
        # 春节休市后 2/5（周三）是 1/28 之后的第一个交易日 → 调仓
        assert scheduler.should_rebalance(cfg, date(2025, 2, 5)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 2, 6)) is False

    def test_day_beyond_month_length_clamps_to_month_end(self) -> None:
        """目标 31 日在 2 月按 28 日处理，而不是整月不调仓。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        cfg = RebalanceConfig(frequency="monthly", day_of_month=31)

        # 2025-02-28（周五）是当月最后一日 → 调仓
        assert scheduler.should_rebalance(cfg, date(2025, 2, 28)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 2, 27)) is False
        # 下一个交易日（3/3 周一）不再触发
        assert scheduler.should_rebalance(cfg, date(2025, 3, 3)) is False
        # 1 月有 31 日 → 仍是 1/31
        assert scheduler.should_rebalance(cfg, date(2025, 1, 31)) is True

    def test_target_day_trading_triggers_once(self) -> None:
        """目标日是交易日时当天触发一次。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        cfg = RebalanceConfig(frequency="monthly", day_of_month=15)

        assert scheduler.should_rebalance(cfg, date(2025, 1, 15)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 1, 16)) is False


class TestGuards:
    """非交易日与日历异常的防线。"""

    def test_non_trading_day_never_rebalances(self) -> None:
        """传入非交易日时一律不调仓（历史缺陷：假期也可能返回 True）。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        cfg = RebalanceConfig(frequency="weekly", day_of_week=4)

        # 2025-05-10 是周六
        assert scheduler.should_rebalance(cfg, date(2025, 5, 10)) is False

    def test_calendar_error_propagates(self) -> None:
        """日历不可用时上抛，而不是退化为按星期比较（C1）。"""
        scheduler = DefaultRebalanceScheduler(_BrokenCalendar())
        cfg = RebalanceConfig(frequency="weekly", day_of_week=1)

        with pytest.raises(TradingCalendarUnavailableError):
            scheduler.should_rebalance(cfg, date(2025, 1, 8))

    def test_daily_does_not_need_calendar(self) -> None:
        """每日调仓不查询日历。"""
        scheduler = DefaultRebalanceScheduler(_BrokenCalendar())
        assert scheduler.should_rebalance(RebalanceConfig(frequency="daily"), date(2025, 1, 8))


class TestRebalanceConfigValidation:
    """调仓配置的强校验（避免拼写错误静默退化为每日调仓）。"""

    def test_unknown_frequency_rejected(self) -> None:
        """未知频率直接拒绝。"""
        with pytest.raises(ValidationError):
            RebalanceConfig(frequency="monthlyy")  # type: ignore[arg-type]

    def test_day_of_week_out_of_range_rejected(self) -> None:
        """周度调仓日只允许 0-4。"""
        with pytest.raises(ValidationError):
            RebalanceConfig(frequency="weekly", day_of_week=7)
        with pytest.raises(ValidationError):
            RebalanceConfig(frequency="weekly", day_of_week=-1)

    def test_day_of_month_out_of_range_rejected(self) -> None:
        """月度调仓日只允许 1-31。"""
        with pytest.raises(ValidationError):
            RebalanceConfig(frequency="monthly", day_of_month=0)
        with pytest.raises(ValidationError):
            RebalanceConfig(frequency="monthly", day_of_month=32)

    def test_valid_bounds_accepted(self) -> None:
        """边界值可接受。"""
        assert RebalanceConfig(frequency="weekly", day_of_week=0).day_of_week == 0
        assert RebalanceConfig(frequency="monthly", day_of_month=31).day_of_month == 31
