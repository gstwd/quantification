"""回测账户逐日累积（纯领域逻辑）。

将"执行引擎输出"与"绩效逐日记账"分离：
回测主循环只负责生成当日持仓/收益，账户累积器负责
累计收益、回撤等随时间变化的账务状态。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


def has_positive_weight(positions: Mapping[str, float] | None) -> bool:
    """判断一组持仓里是否至少有一个正权重（"当日是否持仓"的唯一判定）。

    择时把总仓位降到 0 时，执行器持仓可能是 ``{"000852": 0.0}`` 这类零权重字典；
    用 ``bool(持仓)`` 判断会把空仓日算作持仓日，使 ``active_days`` 偏大、按持仓日
    统计的胜率分母混入空仓日（2026-11-05 独立对账发现，见决策记录）。

    Args:
        positions: 持仓权重，None 视为空仓。

    Returns:
        至少一个资产权重大于 0 时为 True。
    """
    return any(weight > 0 for weight in (positions or {}).values())


@dataclass
class BacktestDayAccumulator:
    """回测账户累积器，跟踪累计净值、峰值与回撤。

    Attributes:
        cumulative: 累计净值，初始 1.0。
        peak: 历史峰值净值，初始 1.0。
        cumulative_return_pct: 累计收益率百分比。
        drawdown_pct: 当前回撤百分比（<=0）。
        daily_returns: 每日收益率列表（百分比，全部交易日）。
        active_returns: 有持仓日的收益率列表（百分比）。
        active_days: 有持仓的交易日计数。
    """

    cumulative: float = 1.0
    peak: float = 1.0
    cumulative_return_pct: float = 0.0
    drawdown_pct: float = 0.0
    daily_returns: list[float] = field(default_factory=list)
    active_returns: list[float] = field(default_factory=list)
    active_days: int = 0

    def apply_day(self, portfolio_return: float, has_positions: bool) -> None:
        """应用单个交易日的组合收益并更新账户状态。

        Args:
            portfolio_return: 当日组合收益率（百分比）。
            has_positions: 当日是否持仓，调用方须用 :func:`has_positive_weight` 判定。
        """
        self.daily_returns.append(portfolio_return)
        if has_positions:
            self.active_returns.append(portfolio_return)
        self.cumulative *= 1 + portfolio_return / 100
        self.cumulative_return_pct = (self.cumulative - 1) * 100
        self.peak = max(self.peak, self.cumulative)
        self.drawdown_pct = (self.cumulative / self.peak - 1) * 100
        if has_positions:
            self.active_days += 1
