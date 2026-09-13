"""个股复权计算纯函数（与 Tushare pro_bar 公式保持一致）。"""

from __future__ import annotations


def adjust_qfq(
    price: float | None,
    adj_factor: float | None,
    latest_adj_factor: float | None,
) -> float | None:
    """计算前复权价格。

    公式：前复权价 = 原始价 × 当日复权因子 / 最新复权因子。

    Args:
        price: 原始价格。
        adj_factor: 当日 Tushare 复权因子。
        latest_adj_factor: 最新交易日复权因子。

    Returns:
        前复权价格；任一输入缺失或最新因子为 0 时返回 None。
    """
    if price is None or adj_factor is None or not latest_adj_factor:
        return None
    return price * adj_factor / latest_adj_factor


def adjust_hfq(price: float | None, adj_factor: float | None) -> float | None:
    """计算后复权价格。

    公式：后复权价 = 原始价 × 当日复权因子。

    Args:
        price: 原始价格。
        adj_factor: 当日 Tushare 复权因子。

    Returns:
        后复权价格；任一输入缺失时返回 None。
    """
    if price is None or adj_factor is None:
        return None
    return price * adj_factor
