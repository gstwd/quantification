"""基准收益计算模块。

提供回测评估所需的基准收益序列：
- 买入持有基准（单一指数）
- 等权组合基准（多指数）
"""

from __future__ import annotations

from datetime import date
from typing import Any


def compute_buy_hold_benchmark(
    all_bars: dict[tuple[str, date], Any],
    index_code: str,
    trading_dates: list[date],
) -> list[float]:
    """计算买入持有策略的日收益率序列。

    以第一个交易日开盘买入、每日按收盘价计算收益。

    Args:
        all_bars: 预加载的指数日线数据，key=(index_code, trade_date)。
        index_code: 基准指数代码，如 "000300"。
        trading_dates: 回测交易日列表。

    Returns:
        日收益率列表（%），与 trading_dates 等长。
    """
    if len(trading_dates) < 2:
        return [0.0] * len(trading_dates)

    returns: list[float] = [0.0]  # 首日无法计算收益
    for i in range(1, len(trading_dates)):
        prev_bar = all_bars.get((index_code, trading_dates[i - 1]))
        curr_bar = all_bars.get((index_code, trading_dates[i]))
        if (
            prev_bar is None
            or curr_bar is None
            or prev_bar.close_price is None
            or curr_bar.close_price is None
            or prev_bar.close_price == 0
        ):
            returns.append(0.0)
        else:
            ret = (curr_bar.close_price / prev_bar.close_price - 1) * 100
            returns.append(round(ret, 4))
    return returns


def compute_equal_weight_benchmark(
    all_bars: dict[tuple[str, date], Any],
    index_codes: list[str],
    trading_dates: list[date],
) -> list[float]:
    """计算等权组合的日收益率序列。

    所有指数等权配置，每日按收盘价计算组合收益。

    Args:
        all_bars: 预加载的指数日线数据，key=(index_code, trade_date)。
        index_codes: 等权组合包含的指数代码列表。
        trading_dates: 回测交易日列表。

    Returns:
        日收益率列表（%），与 trading_dates 等长。
    """
    if len(trading_dates) < 2 or not index_codes:
        return [0.0] * len(trading_dates)

    weight = 1.0 / len(index_codes)
    returns: list[float] = [0.0]

    for i in range(1, len(trading_dates)):
        daily_return = 0.0
        valid_count = 0
        for code in index_codes:
            prev_bar = all_bars.get((code, trading_dates[i - 1]))
            curr_bar = all_bars.get((code, trading_dates[i]))
            if (
                prev_bar is None
                or curr_bar is None
                or prev_bar.close_price is None
                or curr_bar.close_price is None
                or prev_bar.close_price == 0
            ):
                continue
            daily_return += (curr_bar.close_price / prev_bar.close_price - 1) * 100 * weight
            valid_count += 1
        # 有数据失效时按实际有效数量重新归一化
        if valid_count > 0 and valid_count < len(index_codes):
            daily_return = daily_return * len(index_codes) / valid_count
        returns.append(round(daily_return, 4))

    return returns
