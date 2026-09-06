"""行业成分事件去重统计纯函数（不依赖数据库）。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any


def current_membership_counts(
    events: Iterable[tuple[str, str, date]],
    *,
    end_date: date,
) -> dict[str, int]:
    """统计 end_date 时各一级行业的有效成分股数量。

    每只股票取 start_date 不晚于 end_date 的最新成分事件，再按一级行业汇总
    去重后的数量（与扩散重建当日有效成分的口径一致）。

    Args:
        events: (stock_code, industry_code, start_date) 三元组序列。
        end_date: 成分归属生效截止日（含）。

    Returns:
        行业代码 → 成分股数量字典；无有效成分时为空字典。
    """
    best: dict[str, tuple[date, str]] = {}
    for stock_code, industry_code, start_date in events:
        if start_date > end_date:
            continue
        current = best.get(stock_code)
        if current is None or start_date > current[0]:
            best[stock_code] = (start_date, industry_code)
    counts: dict[str, int] = {}
    for _, industry_code in best.values():
        counts[industry_code] = counts.get(industry_code, 0) + 1
    return counts


def normalize_event_rows(
    rows: Iterable[Any],
) -> list[tuple[str, str, date]]:
    """把仓库查询行规范为 (stock_code, industry_code, start_date) 三元组。

    Args:
        rows: 含 stock_code/industry_code/start_date 属性的模型行或
            同序元组/行结果。

    Returns:
        三元组列表。
    """
    result: list[tuple[str, str, date]] = []
    for row in rows:
        if hasattr(row, "stock_code"):
            result.append((row.stock_code, row.industry_code, row.start_date))
        else:
            result.append((str(row[0]), str(row[1]), row[2]))
    return result
