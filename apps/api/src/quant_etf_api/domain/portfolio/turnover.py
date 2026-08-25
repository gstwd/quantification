"""换手率计算（纯领域逻辑）。"""

from __future__ import annotations


def compute_turnover(
    prev_positions: dict[str, float],
    curr_positions: dict[str, float],
) -> float:
    """计算换手率（两日仓位变动的绝对值之和 / 2）。

    Args:
        prev_positions: 前日仓位权重，key=资产代码。
        curr_positions: 当日目标仓位权重，key=资产代码。

    Returns:
        换手率，范围 0-1。
    """
    all_codes = set(prev_positions.keys()) | set(curr_positions.keys())
    turnover = 0.0
    for code in all_codes:
        prev_w = prev_positions.get(code, 0.0)
        curr_w = curr_positions.get(code, 0.0)
        turnover += abs(curr_w - prev_w)
    return turnover / 2.0
