"""个股日线质量统计纯函数（不依赖数据库与外部 API）。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date


def compute_stock_quality(
    *,
    actual_dates: Iterable[date],
    trading_days: Sequence[date],
    expected_start: date | None = None,
    expected_end: date | None = None,
) -> dict[str, date | int | None]:
    """按交易日历全口径统计单只股票的日线质量。

    口径：
    - 期望区间起点 = expected_start（通常为上市日；无上市日时由调用方传入
      数据起点或默认回填起点）；终点 = expected_end（退市日或最近交易日）。
    - 缺失数 = 期望区间内交易日数 - 期望区间内实际有效行数；停牌/长期无成交的
      日期也会被计入缺失，属于本系统的既定口径。
    - 实际日期落在期望区间之外的不参与扣减。

    Args:
        actual_dates: 库内实际存在的日线日期集合。
        trading_days: 按升序排列的交易日集合（仅含期望区间的可用交易日亦可）。
        expected_start: 期望统计起始日；None 时退化为实际最早日期。
        expected_end: 期望统计截止日；None 时退化为实际最晚日期。

    Returns:
        {data_start_date, data_end_date, bar_count, missing_day_count}；
        期望区间无交易日时 missing 记为 0，无任何实际行且无期望范围时返回 None。
    """
    actual = sorted({d for d in actual_dates})
    if not actual:
        data_start: date | None = None
        data_end: date | None = None
        bar_count = 0
    else:
        data_start = actual[0]
        data_end = actual[-1]
        bar_count = len(actual)

    start = expected_start or data_start
    end = expected_end or data_end
    if start is None or end is None:
        return {
            "data_start_date": data_start,
            "data_end_date": data_end,
            "bar_count": bar_count,
            "missing_day_count": None,
        }

    expected = [d for d in trading_days if start <= d <= end]
    if not expected:
        # 期望区间无交易日（例如退市日早于上市日的脏数据），按无缺失处理
        missing = 0
    else:
        actual_in_range = {d for d in actual if start <= d <= end}
        missing = len(expected) - len(actual_in_range)

    return {
        "data_start_date": data_start,
        "data_end_date": data_end,
        "bar_count": bar_count,
        "missing_day_count": max(0, missing),
    }
