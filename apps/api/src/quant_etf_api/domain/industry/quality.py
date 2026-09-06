"""行业指数日线质量统计纯函数（不依赖数据库与外部 API）。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date


def compute_industry_bar_quality(
    *,
    actual_dates: Iterable[date],
    trading_days: Sequence[date],
    expected_end: date | None = None,
) -> dict[str, date | int | None]:
    """按交易日历全口径统计单个申万行业的日线质量。

    口径：
    - 期望起点 = 库内实际最早一根日线（行业指数无“上市日”元数据，缺头
      在全量回填下不会出现，因此不统计最早一根之前的交易日）；
    - 期望终点 = expected_end（通常为最近交易日；为 None 时退化为实际最晚日期）；
    - 缺失数 = [首根实际日线, 期望终点] 内交易日数 - 区间内实际行数；
      中间缺口与尾部滞后都会被计入，与个股口径的“停牌计入缺失”同理，
      补全/重拉后仍可能因上游滞后残留尾部缺失。

    Args:
        actual_dates: 库内实际存在的日线日期集合。
        trading_days: 按升序排列的交易日集合（仅含期望区间的可用交易日亦可）。
        expected_end: 期望统计截止日；None 时退化为实际最晚日期。

    Returns:
        {data_start_date, data_end_date, bar_count, missing_day_count}；
        无任何实际行时 data_start/end 与 missing 均为 None、bar_count 为 0；
        期望区间无交易日时 missing 记为 0。
    """
    actual = sorted({d for d in actual_dates})
    if not actual:
        return {
            "data_start_date": None,
            "data_end_date": None,
            "bar_count": 0,
            "missing_day_count": None,
        }
    data_start = actual[0]
    data_end = actual[-1]
    end = expected_end or data_end
    if data_start > end:
        # 脏数据（期望终点早于首根日线）时按无缺失处理
        missing = 0
    else:
        expected = [d for d in trading_days if data_start <= d <= end]
        actual_in_range = {d for d in actual if d <= end}
        missing = max(0, len(expected) - len(actual_in_range))

    return {
        "data_start_date": data_start,
        "data_end_date": data_end,
        "bar_count": len(actual),
        "missing_day_count": missing,
    }
