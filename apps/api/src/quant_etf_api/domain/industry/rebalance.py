"""行业域调仓相关纯规则。"""

from __future__ import annotations

from datetime import date


def month_end_dates(dates: list[date]) -> list[date]:
    """从升序交易日列表中提取每月最后一个交易日。

    Args:
        dates: 升序交易日列表。

    Returns:
        每月最后一个交易日列表。
    """
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
