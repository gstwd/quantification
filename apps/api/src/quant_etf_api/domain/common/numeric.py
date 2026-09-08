"""通用数值容错规则（纯领域逻辑）。"""

from __future__ import annotations

import math
from typing import Any


def sanitize_metric_value(value: Any) -> Any:
    """将 NaN/Inf 指标值转为 None，避免污染 JSON 结果。

    Args:
        value: 指标值。

    Returns:
        有限数值原样返回，NaN/Inf 转为 None。
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def safe_metric_diff(a: Any, b: Any, digits: int = 2) -> Any:
    """计算两个指标差值的容错版本。

    Args:
        a: 策略 A 指标值。
        b: 策略 B 指标值。
        digits: 保留小数位。

    Returns:
        差值；任一输入为 None/NaN/Inf 时返回 None。
    """
    if a is None or b is None:
        return None
    if isinstance(a, float) and not math.isfinite(a):
        return None
    if isinstance(b, float) and not math.isfinite(b):
        return None
    return round(a - b, digits)


def price_invalid(value: Any) -> bool:
    """判断价格字段是否不可用（None、0 或 NaN）。

    Args:
        value: 价格或 None。

    Returns:
        value 不可用于收益计算时返回 True。
    """
    if value is None or value == 0:
        return True
    try:
        return math.isnan(value)
    except TypeError:
        return False
