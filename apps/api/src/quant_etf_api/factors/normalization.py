"""因子标准化/去极值/中性化模块。

提供横截面标准化函数，用于多因子模型中的量纲统一和异常值处理。
所有函数接受 dict[str, float]（code → value），返回 dict[str, float | None]。
"""

from __future__ import annotations

import math


def normalize_zscore(values: dict[str, float]) -> dict[str, float | None]:
    """横截面 Z-Score 标准化：z_i = (x_i - mean) / std。

    Args:
        values: key=资产代码, value=原始因子值。

    Returns:
        标准化后的因子值，标准差为零时全部返回 0.0。
    """
    if not values:
        return {}
    valid = {k: v for k, v in values.items() if v is not None}
    if len(valid) < 2:
        return {k: 0.0 for k in values}

    n = len(valid)
    mean = sum(valid.values()) / n
    variance = sum((v - mean) ** 2 for v in valid.values()) / (n - 1)
    std = math.sqrt(variance)

    if std == 0:
        return {k: 0.0 for k in values}

    result: dict[str, float | None] = {}
    for k in values:
        if k in valid:
            result[k] = round((valid[k] - mean) / std, 6)
        else:
            result[k] = None
    return result


def normalize_rank(values: dict[str, float]) -> dict[str, float | None]:
    """横截面百分位排名标准化：rank_i = rank(x_i) / N × 100。

    值域 [0, 100]，越大表示在横截面上越靠前。

    Args:
        values: key=资产代码, value=原始因子值。

    Returns:
        排名百分位，缺失值返回 None。
    """
    if not values:
        return {}
    valid_items = [(k, v) for k, v in values.items() if v is not None]
    if not valid_items:
        return {k: None for k in values}

    # 升序排列，rank 从 1 开始
    valid_items.sort(key=lambda x: x[1])
    n = len(valid_items)

    result: dict[str, float | None] = {}
    for k in values:
        if k not in {item[0] for item in valid_items}:
            result[k] = None
            continue

    for rank_idx, (code, _) in enumerate(valid_items, 1):
        result[code] = round(rank_idx / n * 100, 2)

    return result


def normalize_minmax(values: dict[str, float]) -> dict[str, float | None]:
    """横截面 Min-Max 标准化：x' = (x - min) / (max - min) × 100。

    值域 [0, 100]。

    Args:
        values: key=资产代码, value=原始因子值。

    Returns:
        Min-Max 标准化后的因子值。
    """
    if not values:
        return {}
    valid = {k: v for k, v in values.items() if v is not None}
    if len(valid) < 2:
        return {k: 50.0 for k in values}

    vmin = min(valid.values())
    vmax = max(valid.values())
    rng = vmax - vmin

    if rng == 0:
        return {k: 50.0 for k in values}

    result: dict[str, float | None] = {}
    for k in values:
        if k in valid:
            result[k] = round((valid[k] - vmin) / rng * 100, 2)
        else:
            result[k] = None
    return result


def winsorize(
    values: dict[str, float],
    lower_pct: float = 0.01,
    upper_pct: float = 0.99,
) -> dict[str, float]:
    """百分位去极值（Winsorization）。

    将超出 [lower_pct, upper_pct] 分位数的值截断到边界值。

    Args:
        values: key=资产代码, value=原始因子值。
        lower_pct: 下分位数，默认 0.01（1%分位）。
        upper_pct: 上分位数，默认 0.99（99%分位）。

    Returns:
        去极值后的因子值。
    """
    if len(values) < 3:
        return dict(values)

    sorted_vals = sorted(values.values())
    n = len(sorted_vals)

    def _percentile(pct: float) -> float:
        """线性插值计算分位数。"""
        k = (n - 1) * pct
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_vals[int(k)]
        d0 = sorted_vals[int(f)] * (c - k)
        d1 = sorted_vals[int(c)] * (k - f)
        return d0 + d1

    lower_bound = _percentile(lower_pct)
    upper_bound = _percentile(upper_pct)

    result: dict[str, float] = {}
    for k, v in values.items():
        if v < lower_bound:
            result[k] = lower_bound
        elif v > upper_bound:
            result[k] = upper_bound
        else:
            result[k] = v
    return result


def mad_outlier_detect(
    values: dict[str, float], threshold: float = 3.0
) -> dict[str, bool]:
    """MAD（中位数绝对偏差）异常值检测。

    不修改原值，仅标记是否为异常值。

    Args:
        values: key=资产代码, value=原始因子值。
        threshold: MAD 倍数阈值，默认 3.0。

    Returns:
        key=资产代码, value=True 表示异常值。
    """
    if len(values) < 3:
        return {k: False for k in values}

    sorted_vals = sorted(values.values())
    n = len(sorted_vals)
    median = sorted_vals[n // 2] if n % 2 == 1 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2

    abs_deviations = [abs(v - median) for v in sorted_vals]
    abs_deviations.sort()
    mad = abs_deviations[n // 2] if n % 2 == 1 else (abs_deviations[n // 2 - 1] + abs_deviations[n // 2]) / 2

    if mad == 0:
        return {k: False for k in values}

    result: dict[str, bool] = {}
    for k, v in values.items():
        z = 0.6745 * abs(v - median) / mad
        result[k] = z > threshold
    return result
