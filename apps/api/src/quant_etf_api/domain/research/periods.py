"""研究期 / 验证期边界规则（纯领域逻辑）。

量化研究最容易犯的错误是"看了样本外结果再回来改策略"：只要验证期数据被
观察并用于调参，它就不再是样本外。系统因此把"2016-01-01 ~ 2025-12-31 为
研究期、2026-01-01 起为验证期"固化为硬约束：

- 研究类回测（purpose=research）不得越过研究期末端；
- 验证（validation）与监控（monitor）用途允许使用验证期数据，但必须留痕。

本模块只承载纯规则，边界取值由配置层注入，便于单测与复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# 回测用途
PURPOSE_RESEARCH = "research"
PURPOSE_VALIDATION = "validation"
PURPOSE_MONITOR = "monitor"
ALL_PURPOSES = (PURPOSE_RESEARCH, PURPOSE_VALIDATION, PURPOSE_MONITOR)


@dataclass(frozen=True)
class PeriodBoundaries:
    """研究期与验证期的边界定义。

    Attributes:
        research_start: 研究期起始日（含）。
        research_end: 研究期截止日（含），研究类回测的上限。
        validation_start: 验证期起始日（含），通常为研究期截止日的次日。
    """

    research_start: date
    research_end: date
    validation_start: date

    def is_validation_scope(self, end: date) -> bool:
        """判断区间末端是否触及验证期数据。

        Args:
            end: 区间截止日期（含）。

        Returns:
            截止日期晚于研究期末端时返回 True。
        """
        return end > self.research_end


def validate_backtest_period(
    boundaries: PeriodBoundaries,
    purpose: str,
    start: date,
    end: date,
) -> None:
    """校验回测区间是否符合用途对应的边界规则。

    Args:
        boundaries: 研究期/验证期边界定义。
        purpose: 回测用途（research/validation/monitor）。
        start: 区间起始日期（含）。
        end: 区间截止日期（含）。

    Raises:
        ValueError: purpose 非法、区间倒置，或研究类回测越过研究期末端时抛出。
    """
    if purpose not in ALL_PURPOSES:
        raise ValueError(f"未知的回测用途：{purpose}，可选值为 {', '.join(ALL_PURPOSES)}")
    if start > end:
        raise ValueError(f"回测起始日期 {start} 不能晚于截止日期 {end}")
    if purpose == PURPOSE_RESEARCH and boundaries.is_validation_scope(end):
        raise ValueError(
            f"研究类回测不得越过研究期末端 {boundaries.research_end.isoformat()}："
            f"截止日期 {end.isoformat()} 属于验证期数据。"
            "验证期数据只能用于否决，不能用于研究调参；"
            "如需验收请使用 purpose=validation，"
            "上线后监控请使用 purpose=monitor。"
        )
