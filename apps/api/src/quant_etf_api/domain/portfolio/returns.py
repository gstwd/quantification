"""组合收益计算（纯领域逻辑，基于预加载的 K 线数据）。"""

from __future__ import annotations

from datetime import date
from typing import Any


def get_index_return(
    index_code: str,
    trade_date: date,
    next_date: date,
    all_bars: dict[tuple[str, date], Any],
) -> float | None:
    """获取指数 T+1 日收益率（%）。

    Args:
        index_code: 指数代码。
        trade_date: 基准交易日。
        next_date: 收益计算目标日（下一交易日）。
        all_bars: (code, date) → BarRow 的映射，BarRow 需有 .close_price 属性。

    Returns:
        收益率百分比，数据缺失时返回 None。
    """
    today_bar = all_bars.get((index_code, trade_date))
    next_bar = all_bars.get((index_code, next_date))
    if today_bar is None or next_bar is None:
        return None
    if today_bar.close_price is None or next_bar.close_price is None or today_bar.close_price == 0:
        return None
    return round((next_bar.close_price / today_bar.close_price - 1) * 100, 4)


def compute_allocation_return(
    positions: dict[str, float],
    trade_date: date,
    next_date: date | None,
    all_bars: dict[tuple[str, date], Any],
) -> float:
    """按仓位分配方案计算指数组合 T+1 收益。

    Args:
        positions: 仓位权重，key=指数代码。
        trade_date: 基准交易日。
        next_date: 收益计算目标日，None 时返回 0。
        all_bars: (code, date) → BarRow 的映射。

    Returns:
        组合收益率百分比，无仓位或无下一交易日时返回 0.0。
    """
    if next_date is None or not positions:
        return 0.0
    total_return = 0.0
    for code, weight in positions.items():
        ret = get_index_return(code, trade_date, next_date, all_bars)
        if ret is not None:
            total_return += weight * ret
    return round(total_return, 4)
