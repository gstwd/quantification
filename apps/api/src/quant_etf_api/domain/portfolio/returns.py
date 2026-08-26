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


def get_index_rebalance_legs(
    index_code: str,
    trade_date: date,
    next_date: date,
    all_bars: dict[tuple[str, date], Any],
) -> tuple[float | None, float | None]:
    """获取指数 T+1 开盘执行的两段收益率（%）。

    T 日收盘出信号、T+1 日开盘成交时，调仓日收益拆为两段：
    - 隔夜段：旧仓位持有至 T+1 开盘，收益 = open_{T+1} / close_T - 1；
    - 日内段：新仓位自 T+1 开盘买入至收盘，收益 = close_{T+1} / open_{T+1} - 1。
    两段都仅使用 T 及 T+1 的行情，无前视偏差。

    Args:
        index_code: 指数代码。
        trade_date: 信号日 T。
        next_date: 下一交易日 T+1。
        all_bars: (code, date) → BarRow 的映射，BarRow 需有
            close_price / open_price 属性。

    Returns:
        (隔夜段收益, 日内段收益)，任一价格缺失或为零时返回 (None, None)。
    """
    today_bar = all_bars.get((index_code, trade_date))
    next_bar = all_bars.get((index_code, next_date))
    if today_bar is None or next_bar is None:
        return None, None
    close_t = today_bar.close_price
    open_n = next_bar.open_price
    close_n = next_bar.close_price
    if close_t is None or close_t == 0 or open_n is None or open_n == 0 or close_n is None:
        return None, None
    overnight = round((open_n / close_t - 1) * 100, 4)
    intraday = round((close_n / open_n - 1) * 100, 4)
    return overnight, intraday


def compute_rebalance_day_return(
    old_positions: dict[str, float],
    new_positions: dict[str, float],
    trade_date: date,
    next_date: date | None,
    all_bars: dict[tuple[str, date], Any],
) -> float:
    """计算调仓日组合收益（T+1 开盘执行，无滑点）。

    旧仓位持有至 T+1 开盘（吃隔夜段），新仓位自 T+1 开盘买入（吃日内段）；
    某段价格缺失时该仓位该段按 0 处理（与 B10 缺口语义一致）。

    Args:
        old_positions: 调仓前持仓权重。
        new_positions: 调仓后目标权重。
        trade_date: 信号日 T。
        next_date: 下一交易日 T+1，None 时返回 0。
        all_bars: (code, date) → BarRow 的映射。

    Returns:
        组合收益率百分比（四舍五入到 4 位小数）。
    """
    if next_date is None:
        return 0.0
    total_return = 0.0
    for code in set(old_positions) | set(new_positions):
        overnight, intraday = get_index_rebalance_legs(
            code, trade_date, next_date, all_bars
        )
        if overnight is not None and code in old_positions:
            total_return += old_positions[code] * overnight
        if intraday is not None and code in new_positions:
            total_return += new_positions[code] * intraday
    return round(total_return, 4)
