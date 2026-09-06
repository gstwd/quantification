"""行业轮动独立回测纯函数测试。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant_etf_api.domain.industry.backtest import (
    month_end_dates,
    portfolio_stats,
    simulate_rotation_backtest,
)


def test_month_end_dates() -> None:
    """月末日期取每月最后一个交易日。"""
    dates = [
        date(2024, 1, 2),
        date(2024, 1, 31),
        date(2024, 2, 1),
        date(2024, 2, 29),
        date(2024, 3, 4),
    ]
    assert month_end_dates(dates) == [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 4)]


def _panel(base: float) -> pd.DataFrame:
    """构造两个行业三天的价格面板。"""
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    return pd.DataFrame(
        {"801010": [base, base + 1, base + 2], "801030": [base, base + 2, base + 4]}, index=index
    )


def test_simulate_rotation_backtest_simple() -> None:
    """月末信号次日开盘成交，等权组合净值合理递增。"""
    close = _panel(100.0)
    open_ = _panel(99.0)
    # 决策日在 1/3：次日（1/4）开盘按等权买入
    targets = {date(2024, 1, 3): {"801010": 0.5, "801030": 0.5}}
    daily = simulate_rotation_backtest(close=close, open_=open_, targets=targets)
    assert len(daily) == 3
    assert daily.iloc[-1]["nav"] > 1.0
    assert daily.iloc[-1]["positions"] == {"801010": 0.5, "801030": 0.5}


def test_portfolio_stats_fields() -> None:
    """绩效统计包含必要字段。"""
    close = _panel(100.0)
    open_ = _panel(100.0)
    targets = {date(2024, 1, 2): {"801010": 1.0}}
    daily = simulate_rotation_backtest(close=close, open_=open_, targets=targets)
    stats = portfolio_stats(daily)
    for key in (
        "cumulative_return_pct",
        "annual_return_pct",
        "max_drawdown_pct",
        "trading_days",
    ):
        assert key in stats
