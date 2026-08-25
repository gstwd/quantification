"""回测账户逐日累积（纯领域逻辑）。

将"执行引擎输出"与"绩效逐日记账"分离：
回测主循环只负责生成当日持仓/收益，账户累积器负责
累计收益、回撤等随时间变化的账务状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
            has_positions: 当日是否持仓。
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
