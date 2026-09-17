"""调仓顺延（目标日休市）与调仓配置校验单元测试。

覆盖：
- 周度目标日休市：同周顺延到下一个开市日、跨周顺延到下周一，且同一周只触发一次；
- 双周（biweekly）：只有命中奇/偶周才触发，目标日休市顺延归属目标周，跳过的周期
  不会被下一个周期补触发；
- 月度目标日休市：顺延到当月或下月的第一个交易日；目标日超出当月天数时按当月
  最后一日处理（历史缺陷：整月不调仓）；
- 传入非交易日一律不调仓；日历异常仍上抛（C1 不允许降级为按星期近似）；
- 双腿配置的归一化：旧平铺配置升级为两腿同频，单腿 JSON 自动补齐另一条腿；
- ``select_active_legs`` 的四象限判定。
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from quant_etf_api.domain.common.trading_calendar import TradingCalendarUnavailableError
from quant_etf_api.domain.strategies.rebalance import (
    DefaultRebalanceScheduler,
    select_active_legs,
)
from quant_etf_api.engine.config import RebalanceConfig, RebalanceScheduleConfig


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
        cfg = RebalanceScheduleConfig(frequency="weekly", day_of_week=2)

        assert scheduler.should_rebalance(cfg, date(2025, 5, 5)) is False
        assert scheduler.should_rebalance(cfg, date(2025, 5, 6)) is False
        assert scheduler.should_rebalance(cfg, date(2025, 5, 8)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 5, 9)) is False

    def test_carries_forward_across_week_boundary(self) -> None:
        """目标周五休市 → 跨周顺延到下周一（历史缺陷：整周不调仓）。"""
        # 2025-05-09(五) 休市，下一个交易日为 05-12(一)
        scheduler = DefaultRebalanceScheduler(_Calendar({date(2025, 5, 9)}))
        cfg = RebalanceScheduleConfig(frequency="weekly", day_of_week=4)

        assert scheduler.should_rebalance(cfg, date(2025, 5, 8)) is False
        assert scheduler.should_rebalance(cfg, date(2025, 5, 12)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 5, 13)) is False

    def test_target_day_trading_triggers_once(self) -> None:
        """目标日为交易日时当天触发，之后同周不再触发。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        cfg = RebalanceScheduleConfig(frequency="weekly", day_of_week=4)

        assert scheduler.should_rebalance(cfg, date(2025, 5, 9)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 5, 8)) is False

    def test_default_target_is_friday(self) -> None:
        """未指定 day_of_week 时默认周五。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        cfg = RebalanceScheduleConfig(frequency="weekly")

        assert scheduler.should_rebalance(cfg, date(2025, 5, 9)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 5, 8)) is False

    def test_legacy_rebalance_config_still_supported(self) -> None:
        """旧式整个 RebalanceConfig 传入时按选股腿解析（向后兼容）。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        legacy = RebalanceConfig(frequency="weekly", day_of_week=4)

        assert scheduler.should_rebalance(legacy, date(2025, 5, 9)) is True
        assert scheduler.should_rebalance(legacy, date(2025, 5, 8)) is False


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
        cfg = RebalanceScheduleConfig(frequency="monthly", day_of_month=28)

        # 1/27（周一）未到目标日，且上月（12/28）之后已有交易日 → 不调仓
        assert scheduler.should_rebalance(cfg, date(2025, 1, 27)) is False
        # 春节休市后 2/5（周三）是 1/28 之后的第一个交易日 → 调仓
        assert scheduler.should_rebalance(cfg, date(2025, 2, 5)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 2, 6)) is False

    def test_day_beyond_month_length_clamps_to_month_end(self) -> None:
        """目标 31 日在 2 月按 28 日处理，而不是整月不调仓。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        cfg = RebalanceScheduleConfig(frequency="monthly", day_of_month=31)

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
        cfg = RebalanceScheduleConfig(frequency="monthly", day_of_month=15)

        assert scheduler.should_rebalance(cfg, date(2025, 1, 15)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 1, 16)) is False


class TestGuards:
    """非交易日与日历异常的防线。"""

    def test_non_trading_day_never_rebalances(self) -> None:
        """传入非交易日时一律不调仓（历史缺陷：假期也可能返回 True）。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        cfg = RebalanceScheduleConfig(frequency="weekly", day_of_week=4)

        # 2025-05-10 是周六
        assert scheduler.should_rebalance(cfg, date(2025, 5, 10)) is False

    def test_calendar_error_propagates(self) -> None:
        """日历不可用时上抛，而不是退化为按星期比较（C1）。"""
        scheduler = DefaultRebalanceScheduler(_BrokenCalendar())
        cfg = RebalanceScheduleConfig(frequency="weekly", day_of_week=1)

        with pytest.raises(TradingCalendarUnavailableError):
            scheduler.should_rebalance(cfg, date(2025, 1, 8))

    def test_daily_does_not_need_calendar(self) -> None:
        """每日调仓不查询日历。"""
        scheduler = DefaultRebalanceScheduler(_BrokenCalendar())
        assert scheduler.should_rebalance(RebalanceScheduleConfig(frequency="daily"), date(2025, 1, 8))


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

    def test_biweekly_requires_explicit_parity(self) -> None:
        """双周频率必须显式声明奇偶周，避免"以为配了双周、实际按另一周执行"。"""
        with pytest.raises(ValidationError):
            RebalanceScheduleConfig(frequency="biweekly")


class TestLegNormalization:
    """两腿必选：旧配置与单腿配置的归一化。"""

    def test_legacy_flat_config_becomes_two_identical_legs(self) -> None:
        """旧平铺配置升级为两腿同频，从而保持历史回测口径。"""
        config = RebalanceConfig(frequency="weekly", day_of_week=2)

        assert config.selection.frequency == "weekly"
        assert config.selection.day_of_week == 2
        assert config.risk.frequency == "weekly"
        assert config.risk.day_of_week == 2
        # 旧属性仍可读（兼容外部代码）
        assert config.frequency == "weekly"
        assert config.day_of_week == 2

    def test_single_leg_json_fills_the_other_leg(self) -> None:
        """只给一条腿时另一条腿按相同日程补齐（两腿必选）。"""
        only_selection = RebalanceConfig(selection={"frequency": "monthly", "day_of_month": 15})
        assert only_selection.risk.frequency == "monthly"
        assert only_selection.risk.day_of_month == 15

        only_risk = RebalanceConfig(risk={"frequency": "biweekly", "week_parity": "odd"})
        assert only_risk.selection.frequency == "biweekly"
        assert only_risk.selection.week_parity == "odd"

    def test_defaults_are_daily_for_both_legs(self) -> None:
        """未给出任何日程时两腿都默认每日调仓。"""
        config = RebalanceConfig()
        assert config.selection.frequency == "daily"
        assert config.risk.frequency == "daily"


class TestBiweeklySchedule:
    """双周频率的奇偶周命中与顺延。"""

    def test_only_matching_iso_week_triggers(self) -> None:
        """只有命中奇/偶 ISO 周的交易日才触发。"""
        scheduler = DefaultRebalanceScheduler(_Calendar(set()))
        # 2025-01-10 是 ISO 第 2 周（偶）；2025-01-17 是第 3 周（奇）
        even = RebalanceScheduleConfig(frequency="biweekly", week_parity="even", day_of_week=4)
        odd = RebalanceScheduleConfig(frequency="biweekly", week_parity="odd", day_of_week=4)
        assert scheduler.should_rebalance(even, date(2025, 1, 10))
        assert not scheduler.should_rebalance(even, date(2025, 1, 17))
        assert not scheduler.should_rebalance(odd, date(2025, 1, 10))
        assert scheduler.should_rebalance(odd, date(2025, 1, 17))

    def test_holiday_target_carries_into_next_week(self) -> None:
        """命中周的周五休市 → 顺延到下周一（跨周顺延），且此后不再重复触发。"""
        scheduler = DefaultRebalanceScheduler(_Calendar({date(2025, 1, 10)}))
        even = RebalanceScheduleConfig(frequency="biweekly", week_parity="even", day_of_week=4)

        # 01-13（周一）是 01-10（目标日）之后的第一个交易日 → 触发
        assert scheduler.should_rebalance(even, date(2025, 1, 13)) is True
        # 此后 01-16 仍落在同一目标周窗口内但已非"之后第一个交易日" → 不触发；
        # 01-16 是交易日，因此顺延窗口在 01-16 关闭
        assert scheduler.should_rebalance(even, date(2025, 1, 16)) is False
        assert scheduler.should_rebalance(even, date(2025, 1, 14)) is False
        # 下一个（奇数）周的周四不触发
        assert scheduler.should_rebalance(even, date(2025, 1, 23)) is False

    def test_skipped_cycle_is_not_retriggered(self) -> None:
        """未命中周期的目标日即使休市也不在下个周期补触发。"""
        # 2025-01-17（奇数周周五）休市 → 01-20（周一，仍是奇数周）顺延触发；
        # 偶周配置不应在 01-20 触发（历史缺陷：跨周期误补触发）
        scheduler = DefaultRebalanceScheduler(_Calendar({date(2025, 1, 17)}))
        even = RebalanceScheduleConfig(frequency="biweekly", week_parity="even", day_of_week=4)
        odd = RebalanceScheduleConfig(frequency="biweekly", week_parity="odd", day_of_week=4)

        assert scheduler.should_rebalance(even, date(2025, 1, 20)) is False
        assert scheduler.should_rebalance(odd, date(2025, 1, 20)) is True
        # 下一个偶周（2025-01-24 周五）正常触发
        assert scheduler.should_rebalance(even, date(2025, 1, 24)) is True
        assert scheduler.should_rebalance(even, date(2025, 1, 27)) is False


class TestSelectActiveLegs:
    """两腿到期的四象限判定。"""

    def _scheduler(self) -> DefaultRebalanceScheduler:
        """构造无休市日的调度器。"""
        return DefaultRebalanceScheduler(_Calendar(set()))

    def test_no_rebalance_config_means_daily_both_legs(self) -> None:
        """未配置调仓模块时两腿都视为每日调仓。"""
        legs = select_active_legs(self._scheduler(), None, date(2025, 1, 8))
        assert legs.selection is True
        assert legs.risk is True

    def test_identical_legs_fire_together(self) -> None:
        """两腿同频（旧配置升级后的形态）在同一天一起到期。"""
        config = RebalanceConfig(frequency="weekly", day_of_week=4)
        scheduler = self._scheduler()

        legs = select_active_legs(scheduler, config, date(2025, 1, 10))
        assert (legs.selection, legs.risk) == (True, True)
        legs_other = select_active_legs(scheduler, config, date(2025, 1, 9))
        assert (legs_other.selection, legs_other.risk) == (False, False)

    def test_split_legs_fire_independently(self) -> None:
        """两腿频率不同时各自独立到期（选股腿周一、风险腿每日）。"""
        config = RebalanceConfig(
            selection={"frequency": "weekly", "day_of_week": 0},
            risk={"frequency": "daily"},
        )
        scheduler = self._scheduler()

        # 周一：两腿都到期
        monday = select_active_legs(scheduler, config, date(2025, 1, 6))
        assert (monday.selection, monday.risk) == (True, True)
        # 周二：只有风险腿到期
        tuesday = select_active_legs(scheduler, config, date(2025, 1, 7))
        assert (tuesday.selection, tuesday.risk) == (False, True)
