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

    与策略主循环的"决策日归因"口径对齐：第 i 个元素表示
    [收盘 T_i, 收盘 T_{i+1}] 的行情收益（决策日 T_i 落账，
    对应 T+1 交易日实际实现的行情）；最后一个交易日无下一日
    收盘价，收益记 0。等长配对后策略/基准逐日指标（Alpha/Beta/
    信息比率/跟踪误差）与权益曲线叠加不再错位一个交易日。

    Args:
        all_bars: 预加载的指数日线数据，key=(index_code, trade_date)。
        index_code: 基准指数代码，如 "000300"。
        trading_dates: 回测交易日列表。

    Returns:
        日收益率列表（%），与 trading_dates 等长。
    """
    if len(trading_dates) < 2:
        return [0.0] * len(trading_dates)

    returns: list[float] = []
    for i in range(len(trading_dates) - 1):
        today_bar = all_bars.get((index_code, trading_dates[i]))
        next_bar = all_bars.get((index_code, trading_dates[i + 1]))
        if (
            today_bar is None
            or next_bar is None
            or today_bar.close_price is None
            or next_bar.close_price is None
            or today_bar.close_price == 0
        ):
            returns.append(0.0)
        else:
            ret = (next_bar.close_price / today_bar.close_price - 1) * 100
            returns.append(round(ret, 4))
    # 最后一个交易日无下一交易日，按策略主循环相同约定记 0
    returns.append(0.0)
    return returns


def compute_equal_weight_benchmark(
    all_bars: dict[tuple[str, date], Any],
    index_codes: list[str],
    trading_dates: list[date],
) -> list[float]:
    """计算等权组合的日收益率序列。

    口径与 compute_buy_hold_benchmark 一致（决策日归因）：
    第 i 个元素为组合在 [收盘 T_i, 收盘 T_{i+1}] 的等权收益，
    最后一个交易日收益记 0。

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
    returns: list[float] = []

    for i in range(len(trading_dates) - 1):
        daily_return = 0.0
        valid_count = 0
        for code in index_codes:
            today_bar = all_bars.get((code, trading_dates[i]))
            next_bar = all_bars.get((code, trading_dates[i + 1]))
            if (
                today_bar is None
                or next_bar is None
                or today_bar.close_price is None
                or next_bar.close_price is None
                or today_bar.close_price == 0
            ):
                continue
            daily_return += (next_bar.close_price / today_bar.close_price - 1) * 100 * weight
            valid_count += 1
        # 有数据失效时按实际有效数量重新归一化
        if valid_count > 0 and valid_count < len(index_codes):
            daily_return = daily_return * len(index_codes) / valid_count
        returns.append(round(daily_return, 4))

    # 最后一个交易日无下一交易日，收益记 0
    returns.append(0.0)
    return returns
