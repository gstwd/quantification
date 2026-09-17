"""调仓规则（纯领域逻辑）。

支持 daily / weekly / biweekly / monthly 频率，并支持交易日历对齐：
调仓目标日如遇非交易日，自动顺延至该目标日之后（含）的第一个交易日，
可跨自然周、跨自然月，不受固定天数窗口限制。
领域层仅依赖 TradingCalendarLike 协议，不依赖任何 infra 实现。

严格口径（C1）：交易日历为**必填依赖**，不再提供"未注入时降级为周末判断"
的兜底实现。日历不可用时异常直接上抛，避免同一配置在不同环境下产生
两套调仓日历、进而产生两套回测结果。
"""

from __future__ import annotations

import logging
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from quant_etf_api.domain.common.trading_calendar import TradingCalendarLike
from quant_etf_api.engine.config import (
    RebalanceConfig,
    RebalanceScheduleConfig,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RebalanceLegs:
    """当日到期的调仓腿。

    Attributes:
        selection: 选股腿是否到期（重建组合成分与权重）。
        risk: 风险腿是否到期（只按择时等比缩放现有成分的总仓位）。
    """

    selection: bool
    risk: bool


def _matches_week_parity(day: date, week_parity: str | None) -> bool:
    """判断某个自然日所属的 ISO 周是否命中给定奇偶周。

    以 ISO 周序号跨年稳定锚定：``odd`` 命中奇数周、``even`` 命中偶数周，
    其余取值按 ``even`` 处理（配置层已做枚举校验，此处为防守）。

    Args:
        day: 待判定的自然日。
        week_parity: ``odd`` / ``even`` / None。

    Returns:
        该自然日所属 ISO 周是否命中。
    """
    expected = 1 if week_parity == "odd" else 0
    return day.isocalendar().week % 2 == expected


class RebalanceScheduler(Protocol):
    """调仓调度器协议。"""

    def should_rebalance(
        self,
        config: RebalanceScheduleConfig,
        current_date: date,
        last_rebalance_date: date | None = None,
    ) -> bool:
        """判断某条腿是否应该在当前交易日调仓。

        Args:
            config: 单条调仓腿的频率配置。
            current_date: 当前日期。
            last_rebalance_date: 上次调仓日期。

        Returns:
            是否应该调仓。
        """
        ...


class DefaultRebalanceScheduler:
    """默认调仓调度器（纯领域实现）。

    交易日历通过 TradingCalendarLike 协议**必填**注入；不提供任何降级实现，
    因此周度/月度调仓的对齐结果完全由真实日历决定（可复现、可比对）。
    """

    def __init__(self, trading_calendar: TradingCalendarLike) -> None:
        """初始化调仓调度器。

        Args:
            trading_calendar: 交易日历实现（上游数据源或本地日历表快照）。

        Raises:
            ValueError: 未提供交易日历时抛出（严格口径，不允许降级）。
        """
        if trading_calendar is None:
            raise ValueError(
                "交易日历为必填依赖：系统不允许按星期近似判断交易日，"
                "请先通过 resolve_trading_calendar() 解析出有效日历"
            )
        self._cal = trading_calendar

    def should_rebalance(
        self,
        config: RebalanceScheduleConfig,
        current_date: date,
        last_rebalance_date: date | None = None,
    ) -> bool:
        """判断某一条腿是否应该在当前交易日调仓。

        对齐语义为"目标日（含）之后的第一个交易日"：
        - daily：每个交易日都调仓。
        - weekly：目标 weekday（默认 4=周五）取 current_date 所在自然周内不晚于
          current_date 的那一天，若该周尚未到目标 weekday，则取上一自然周的目标日；
          再判断 current_date 是否为目标日之后（含）的第一个交易日。
        - biweekly：先按 ISO 周序奇偶判断目标周是否命中（``week_parity``），命中时与
          weekly 同样顺延；未命中则整体前移一个周期（14 天）后再判定，因此**跳过的
          周期里休市的目标日不会在下一个周期被补触发**。
        - monthly：目标 day_of_month（默认 1）同上；目标日超出当月天数时按当月
          最后一日处理。

        由此，目标日休市时会顺延到下一个开市日，且既支持同周顺延（周三休市 →
          周四开市）、也支持跨周（周五休市 → 下周一）与跨月（1 月 28 日休市 →
          2 月首个交易日）顺延；同一自然周/周期/月内至多触发一次。

        Args:
            config: 单条调仓腿的频率配置（旧式 ``RebalanceConfig`` 亦被兼容，
                按其中的选股腿解析）。
            current_date: 当前日期（调用方应只传交易日）。
            last_rebalance_date: 上次调仓日期。本实现**不使用**该参数：同一自然
                周/周期/月内"目标日之后的第一个交易日"唯一，无需额外状态即可判定。

        Returns:
            是否应该调仓。

        Raises:
            TradingCalendarUnavailableError: 交易日历不可用时抛出。
            ValueError: 调仓频率不在 daily/weekly/biweekly/monthly 之内时抛出
                （配置层已做枚举校验，此处为防守，避免静默退化为每日调仓）。
        """
        # 旧调用点可能传入整个 RebalanceConfig；此处取其选股腿，保持向后兼容。
        schedule = config.selection if isinstance(config, RebalanceConfig) else config
        if schedule.frequency == "daily":
            return True

        if schedule.frequency == "weekly":
            target_day = schedule.day_of_week if schedule.day_of_week is not None else 4
            # 距最近一次目标 weekday 的天数（0 表示今天就是目标日）
            delta = (current_date.weekday() - target_day) % 7
            return self._is_carried_target(current_date, current_date - timedelta(days=delta))

        if schedule.frequency == "biweekly":
            target_day = schedule.day_of_week if schedule.day_of_week is not None else 4
            delta = (current_date.weekday() - target_day) % 7
            target_date = current_date - timedelta(days=delta)
            if not _matches_week_parity(target_date, schedule.week_parity):
                # 非命中周期：整体前移一个双周周期，避免在"空周期"里误触发
                target_date -= timedelta(days=14)
            return self._is_carried_target(current_date, target_date)

        if schedule.frequency == "monthly":
            target = schedule.day_of_month if schedule.day_of_month is not None else 1
            this_month_last = monthrange(current_date.year, current_date.month)[1]
            this_month_target = min(target, this_month_last)
            if current_date.day >= this_month_target:
                target_date = current_date.replace(day=this_month_target)
            else:
                prev_month_last = current_date.replace(day=1) - timedelta(days=1)
                prev_month_target = min(
                    target, monthrange(prev_month_last.year, prev_month_last.month)[1]
                )
                target_date = prev_month_last.replace(day=prev_month_target)
            return self._is_carried_target(current_date, target_date)

        raise ValueError(
            f"未知的调仓频率 {schedule.frequency!r}：可选 daily / weekly / biweekly / monthly"
        )

    def _is_carried_target(self, current_date: date, target_date: date) -> bool:
        """判断当前日是否为目标日（含）之后的第一个交易日。

        算法：从目标日逐日推进到 current_date 之前，若中途存在交易日，说明调仓
        已在更早的交易日发生，当前日不再调仓；否则当前日就是顺延后的调仓日。
        不设固定天数上限，因此春节等超长休市也能正确顺延。

        日历异常（TradingCalendarUnavailableError）**直接上抛**，不再降级为
        "按星期比较"——那正是 C1 记录的静默口径分叉来源。

        Args:
            current_date: 当前日期。
            target_date: 目标调仓日（自然日，可能早于 current_date）。

        Returns:
            是否应在当前日调仓。

        Raises:
            TradingCalendarUnavailableError: 交易日历不可用时抛出。
        """
        if not self._cal.is_trading_day(current_date):
            # 调用方应只传交易日；非交易日一律不调仓（历史缺陷：
            # "current_date.weekday() == 目标日" 会让休市日也返回 True）
            logger.warning("调仓判定收到非交易日 %s，按不调仓处理", current_date)
            return False
        check = target_date
        while check < current_date:
            if self._cal.is_trading_day(check):
                return False
            check += timedelta(days=1)
        return True


def select_active_legs(
    scheduler: RebalanceScheduler,
    rebalance: RebalanceConfig | None,
    current_date: date,
    last_selection_date: date | None = None,
    last_risk_date: date | None = None,
) -> RebalanceLegs:
    """判定当日到期的调仓腿。

    口径（与回测/实时两侧共用）：

    - ``rebalance`` 为 None（未配置调仓模块）→ 两腿都视为"每日"，即每日重建；
    - 两腿频率一致（旧配置升级后的默认形态）→ 与改造前的单腿口径完全一致；
    - 两腿频率不同 → 只在各自到期的交易日生效，未到期的腿返回 False。

    Args:
        scheduler: 调仓调度器（交易日历必填的严格实现）。
        rebalance: 双腿调仓配置，None 表示未配置。
        current_date: 当前交易日。
        last_selection_date: 上次选股腿调仓日（当前实现不参与判定，保留签名）。
        last_risk_date: 上次风险腿调仓日（当前实现不参与判定，保留签名）。

    Returns:
        两腿到期情况。
    """
    if rebalance is None:
        return RebalanceLegs(selection=True, risk=True)
    return RebalanceLegs(
        selection=scheduler.should_rebalance(rebalance.selection, current_date, last_selection_date),
        risk=scheduler.should_rebalance(rebalance.risk, current_date, last_risk_date),
    )
