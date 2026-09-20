"""K 线衍生指标计算函数（纯领域逻辑，无外部依赖）。

函数参数统一使用指数代码。
"""

from __future__ import annotations

from datetime import date
from typing import Any


def calc_volume_ratio_20d(
    code: str, trade_date: date, all_bars: dict[tuple[str, date], Any]
) -> float | None:
    """计算 20 日量比：当日成交量 / 近 20 个交易日平均成交量。

    取 trade_date 之前按日期最近的 20 个有成交量的交易日作为分母；
    可用历史不足 20 个交易日时按实际条数平均。

    Args:
        code: 指数代码。
        trade_date: 目标交易日。
        all_bars: (code, date) → BarRow 的映射，BarRow 需有 .volume 属性。

    Returns:
        量比，数据不足时返回 None（区分"无数据"与"量比恰好为 1"）。
    """
    today_bar = all_bars.get((code, trade_date))
    if today_bar is None or today_bar.volume is None:
        return None
    past = sorted(
        (dt, v.volume)
        for (c, dt), v in all_bars.items()
        if c == code and dt < trade_date and v.volume is not None
    )
    if not past:
        return None
    recent = [volume for _dt, volume in past[-20:]]
    avg = sum(recent) / len(recent)
    return round(today_bar.volume / avg, 4) if avg > 0 else None


def calc_volume_ratio_17d(
    code: str, trade_date: date, all_bars: dict[tuple[str, date], Any]
) -> float | None:
    """计算 17 日量比：当日成交量 / 近 17 个交易日平均成交量。

    取 trade_date 之前按日期最近的 17 个有成交量的交易日作为分母；
    可用历史不足 17 个交易日时按实际条数平均。

    Args:
        code: 指数代码。
        trade_date: 目标交易日。
        all_bars: (code, date) → BarRow 的映射，BarRow 需有 .volume 属性。

    Returns:
        量比，数据不足时返回 None（区分"无数据"与"量比恰好为 1"）。
    """
    today_bar = all_bars.get((code, trade_date))
    if today_bar is None or today_bar.volume is None:
        return None
    past = sorted(
        (dt, v.volume)
        for (c, dt), v in all_bars.items()
        if c == code and dt < trade_date and v.volume is not None
    )
    if not past:
        return None
    recent = [volume for _dt, volume in past[-17:]]
    avg = sum(recent) / len(recent)
    return round(today_bar.volume / avg, 4) if avg > 0 else None


def calc_5d_return(code: str, trade_date: date, all_bars: dict[tuple[str, date], Any]) -> float:
    """计算近 5 日收益率（%）。

    适用于指数 K 线数据。

    Args:
        code: 指数代码。
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
