"""K 线衍生指标计算函数（纯领域逻辑，无外部依赖）。

函数参数统一使用 code（可以是 ETF 代码或指数代码），
因为 EtfDailyBarModel 和 IndexDailyBarModel 字段结构相同。
"""

from __future__ import annotations

from datetime import date
from typing import Any


def calc_volume_ratio_20d(
    code: str, trade_date: date, all_bars: dict[tuple[str, date], Any]
) -> float | None:
    """计算 20 日量比：当日成交量 / 近 20 日平均成交量。

    Args:
        code: 资产代码（ETF 或指数）。
        trade_date: 目标交易日。
        all_bars: (code, date) → BarRow 的映射，BarRow 需有 .volume 属性。

    Returns:
        量比，数据不足时返回 None（区分"无数据"与"量比恰好为 1"）。
    """
    today_bar = all_bars.get((code, trade_date))
    if today_bar is None or today_bar.volume is None:
        return None
    past_volumes = [
        v.volume
        for (c, dt), v in all_bars.items()
        if c == code and dt < trade_date and v.volume is not None
    ]
    past_volumes.sort()
    recent_20 = past_volumes[-20:] if len(past_volumes) >= 20 else past_volumes
    if not recent_20:
        return None
    avg = sum(recent_20) / len(recent_20)
    return round(today_bar.volume / avg, 4) if avg > 0 else None


def calc_volume_ratio_17d(
    code: str, trade_date: date, all_bars: dict[tuple[str, date], Any]
) -> float | None:
    """计算 17 日量比：当日成交量 / 近 17 日平均成交量。

    Args:
        code: 资产代码（ETF 或指数）。
        trade_date: 目标交易日。
        all_bars: (code, date) → BarRow 的映射，BarRow 需有 .volume 属性。

    Returns:
        量比，数据不足时返回 None（区分"无数据"与"量比恰好为 1"）。
    """
    today_bar = all_bars.get((code, trade_date))
    if today_bar is None or today_bar.volume is None:
        return None
    past_volumes = [
        v.volume
        for (c, dt), v in all_bars.items()
        if c == code and dt < trade_date and v.volume is not None
    ]
    past_volumes.sort()
    recent_17 = past_volumes[-17:] if len(past_volumes) >= 17 else past_volumes
    if not recent_17:
        return None
    avg = sum(recent_17) / len(recent_17)
    return round(today_bar.volume / avg, 4) if avg > 0 else None


def calc_5d_return(code: str, trade_date: date, all_bars: dict[tuple[str, date], Any]) -> float:
    """计算近 5 日收益率（%）。

    适用于 ETF 和指数，因为两者 K 线结构相同。

    Args:
        code: 资产代码（ETF 或指数）。
        trade_date: 目标交易日。
        all_bars: (code, date) → BarRow 的映射，BarRow 需有 .close_price 属性。

    Returns:
        5 日收益率（%），默认 0.0。
    """
    today_bar = all_bars.get((code, trade_date))
    if today_bar is None or today_bar.close_price is None:
        return 0.0
    past_closes = sorted(
        [
            (dt, v.close_price)
            for (c, dt), v in all_bars.items()
            if c == code and dt < trade_date and v.close_price is not None
        ],
        key=lambda x: x[0],
    )
    if len(past_closes) < 5:
        return 0.0
    base_close = past_closes[-5][1]
    return round((today_bar.close_price / base_close - 1) * 100, 4) if base_close > 0 else 0.0
