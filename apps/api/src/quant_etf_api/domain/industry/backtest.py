"""行业轮动独立回测纯函数（T 日收盘出信号、T+1 开盘成交）。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


def month_end_dates(dates: list[date]) -> list[date]:
    """从升序交易日列表中提取每月最后一个交易日。"""
    result: list[date] = []
    i = 0
    while i < len(dates):
        j = i
        while (
            j < len(dates) and dates[j].year == dates[i].year and dates[j].month == dates[i].month
        ):
            j += 1
        result.append(dates[j - 1])
        i = j
    return result


def simulate_rotation_backtest(
    *,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    targets: dict[date, dict[str, float]],
) -> pd.DataFrame:
    """模拟月末调仓净值（无费用无滑点）。

    Args:
        close: date × industry 收盘价宽表。
        open_: date × industry 开盘价宽表。
        targets: 决策日 → 目标权重（决策日收盘出信号）。

    Returns:
        逐日 DataFrame，列为 return_pct/nav/positions。
    """
    dates = list(close.index)
    nav = 1.0
    rows: list[dict[str, Any]] = []
    positions: dict[str, float] = {}
    prev_close: pd.Series | None = None
    for i, day in enumerate(dates):
        next_day = dates[i + 1] if i + 1 < len(dates) else None
        close_row = close.loc[day]
        decision_key = day.date() if hasattr(day, "date") else day
        if decision_key in targets and next_day is not None:
            new_target = targets[decision_key]
            next_open = open_.loc[next_day]
            next_close = close.loc[next_day]
            old_leg = 0.0
            for code, weight in positions.items():
                if code in close_row.index and code in next_open.index:
                    old_leg += weight * (next_open[code] / close_row[code] - 1.0)
            new_leg = 0.0
            for code, weight in new_target.items():
                if code in next_open.index and code in next_close.index:
                    new_leg += weight * (next_close[code] / next_open[code] - 1.0)
            day_return = old_leg + new_leg
            positions = dict(new_target)
        else:
            day_return = 0.0
            if prev_close is not None:
                for code, weight in positions.items():
                    if code in prev_close.index and code in close_row.index:
                        day_return += weight * (close_row[code] / prev_close[code] - 1.0)
            else:
                day_return = 0.0
        prev_close = close_row
        nav *= 1.0 + day_return
        rows.append(
            {
                "trade_date": day,
                "return_pct": round(day_return * 100.0, 6),
                "nav": round(nav, 6),
                "positions": dict(positions),
            }
        )
    return pd.DataFrame(rows)


def portfolio_stats(daily: pd.DataFrame) -> dict[str, float]:
    """计算独立回测的汇总绩效（无风险利率 0）。"""
    returns = daily["return_pct"] / 100.0
    n = len(returns)
    if n == 0:
        return {}
    nav = daily["nav"].iloc[-1]
    years = n / 252.0
    annual = (nav ** (1.0 / years) - 1.0) if years > 0 else 0.0
    cummax = daily["nav"].cummax()
    drawdown = (daily["nav"] / cummax - 1.0).min()
    vol = float(returns.std(ddof=1) * (252.0**0.5)) if n > 1 else 0.0
    sharpe = (
        float(returns.mean() / returns.std(ddof=1) * (252.0**0.5))
        if n > 1 and returns.std(ddof=1) > 0
        else 0.0
    )
    turnover = 0.0
    prev = {}
    for _, row in daily.iterrows():
        current = row["positions"]
        if current and prev:
            turnover += (
                sum(
                    abs(current.get(code, 0.0) - prev.get(code, 0.0))
                    for code in set(current) | set(prev)
                )
                / 2.0
            )
        prev = current
    return {
        "cumulative_return_pct": round((nav - 1.0) * 100.0, 4),
        "annual_return_pct": round(annual * 100.0, 4),
        "max_drawdown_pct": round(drawdown * 100.0, 4),
        "annual_volatility_pct": round(vol * 100.0, 4),
        "sharpe": round(sharpe, 4),
        "total_turnover": round(turnover, 4),
        "trading_days": n,
    }
