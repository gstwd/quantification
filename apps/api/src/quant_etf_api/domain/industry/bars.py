"""行业指数日线派生字段纯函数（前收盘与涨跌幅）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def derive_prev_close_change(
    rows: Sequence[dict[str, Any]],
    *,
    prev_close_override: float | None = None,
) -> list[dict[str, Any]]:
    """按同一行业代码升序计算 prev_close_price 与 change_pct。

    每行以“上一根日线 close”作为前收盘；prev_close_override 用于外部传入
    拉取窗口之前的库内最近收盘，避免窗口首日误判为无前收。首行无前收时
    prev_close_price 与 change_pct 均为 None。

    Args:
        rows: 按 trade_date 升序的行业日线字典列表（须含 close_price）。
        prev_close_override: 可选的前一根收盘价，通常为库内窗口前最近一行。

    Returns:
        新字典列表，浅拷贝自输入并补充 prev_close_price/change_pct 字段；
        输入为 None 的 close 行不产出派生值（保持 None）。
    """
    result: list[dict[str, Any]] = []
    prev_close = prev_close_override
    for row in rows:
        item = dict(row)
        close = item.get("close_price")
        if close is None or prev_close is None:
            item["prev_close_price"] = None
            item["change_pct"] = None
        else:
            item["prev_close_price"] = prev_close
            item["change_pct"] = (float(close) / float(prev_close) - 1.0) * 100.0
        prev_close = close
        result.append(item)
    return result
