"""walk-forward 滚动样本外验证的窗口切分纯函数。"""

from __future__ import annotations

from datetime import date


def compute_folds(trade_dates: list[date], k: int) -> list[tuple[date, date]]:
    """将交易日序列均分为 k 个连续验证窗口。

    系统不内置参数搜索：候选配置为静态策略，"训练"由 agent 在轮次间
    修改配置完成，本函数只负责把评估区间切成等宽的滚动样本外验证窗。

    Args:
        trade_dates: 升序、去重的交易日列表。
        k: 验证窗口数量，必须 >= 1。

    Returns:
        每折的 (start, end) 列表，按时间升序，区间均含首尾日期。

    Raises:
        ValueError: k < 1，或交易日数量不足 k。
    """
    if k < 1:
        raise ValueError("folds 必须 >= 1")
    if len(trade_dates) < k:
        raise ValueError(
            f"区间内交易日不足：需要至少 {k} 个，实际只有 {len(trade_dates)} 个"
        )

    chunk_size = len(trade_dates) // k
    folds: list[tuple[date, date]] = []
    for i in range(k):
        start_idx = i * chunk_size
        end_idx = len(trade_dates) if i == k - 1 else (i + 1) * chunk_size
        folds.append((trade_dates[start_idx], trade_dates[end_idx - 1]))
    return folds
