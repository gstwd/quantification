"""K 线衍生指标计算函数（纯领域逻辑，无外部依赖）。"""

from __future__ import annotations

from datetime import date
from typing import Any


def calc_volume_ratio_20d(
    etf_code: str, trade_date: date, all_bars: dict[tuple[str, date], Any]
) -> float:
    """计算 20 日量比：当日成交量 / 近 20 日平均成交量。

    Args:
        etf_code: ETF 代码
        trade_date: 目标交易日
        all_bars: (code, date) → BarRow 的映射，BarRow 需有 .volume 属性

    Returns:
        量比，默认 1.0
    """
    today_bar = all_bars.get((etf_code, trade_date))
    if today_bar is None or today_bar.volume is None:
        return 1.0
    past_volumes = [
        v.volume
        for (code, dt), v in all_bars.items()
        if code == etf_code and dt < trade_date and v.volume is not None
    ]
    past_volumes.sort()
    recent_20 = past_volumes[-20:] if len(past_volumes) >= 20 else past_volumes
    if not recent_20:
        return 1.0
    avg = sum(recent_20) / len(recent_20)
    return round(today_bar.volume / avg, 4) if avg > 0 else 1.0


def calc_5d_return_etf(
    etf_code: str, trade_date: date, all_bars: dict[tuple[str, date], Any]
) -> float:
    """计算 ETF 近 5 日收益率（%）。

    Args:
        etf_code: ETF 代码
        trade_date: 目标交易日
        all_bars: (code, date) → BarRow 的映射，BarRow 需有 .close_price 属性

    Returns:
        5 日收益率（%），默认 0.0
    """
    today_bar = all_bars.get((etf_code, trade_date))
    if today_bar is None or today_bar.close_price is None:
        return 0.0
    past_closes = sorted(
        [
            (dt, v.close_price)
            for (code, dt), v in all_bars.items()
            if code == etf_code and dt < trade_date and v.close_price is not None
        ],
        key=lambda x: x[0],
    )
    if len(past_closes) < 5:
        return 0.0
    base_close = past_closes[-5][1]
    return round((today_bar.close_price / base_close - 1) * 100, 4) if base_close > 0 else 0.0


def calc_5d_return_index(
    index_code: str, trade_date: date, all_index_bars: dict[tuple[str, date], Any]
) -> float:
    """计算指数近 5 日收益率（%）。

    Args:
        index_code: 指数代码
        trade_date: 目标交易日
        all_index_bars: (code, date) → BarRow 的映射，BarRow 需有 .close_price 属性

    Returns:
        5 日收益率（%），默认 0.0
    """
    today_bar = all_index_bars.get((index_code, trade_date))
    if today_bar is None or today_bar.close_price is None:
        return 0.0
    past_closes = sorted(
        [
            (dt, v.close_price)
            for (code, dt), v in all_index_bars.items()
            if code == index_code and dt < trade_date and v.close_price is not None
        ],
        key=lambda x: x[0],
    )
    if len(past_closes) < 5:
        return 0.0
    base_close = past_closes[-5][1]
    return round((today_bar.close_price / base_close - 1) * 100, 4) if base_close > 0 else 0.0
