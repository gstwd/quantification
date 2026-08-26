"""因子变换函数注册表（独立扩展点）。

策略配置中的 transforms 引用的变换函数统一在此注册。
新增变换只需调用 register_transform()，无需改动评分器本体，
降低"配置驱动、无需代码"定位与硬编码因子语义之间的摩擦（7.4#1）。
"""

from __future__ import annotations

from typing import Callable

# 变换函数注册表：name → 单参数纯函数（float → float）
_TRANSFORM_REGISTRY: dict[str, Callable[[float], float]] = {}


def register_transform(name: str, fn: Callable[[float], float]) -> None:
    """注册一个变换函数。

    Args:
        name: 变换函数名称（策略配置中引用）。
        fn: 单参数纯函数，接收因子原始值，返回 0-100 得分。

    Raises:
        ValueError: 名称已被占用。
    """
    if name in _TRANSFORM_REGISTRY:
        raise ValueError(f"变换函数 {name} 已注册，请勿重复注册")
    _TRANSFORM_REGISTRY[name] = fn


def get_transform(name: str) -> Callable[[float], float]:
    """获取变换函数。

    Args:
        name: 变换函数名称。

    Returns:
        变换函数。

    Raises:
        KeyError: 未知的变换函数名称。
    """
    if name not in _TRANSFORM_REGISTRY:
        raise KeyError(f"未知的变换函数: {name}，可用: {list(_TRANSFORM_REGISTRY.keys())}")
    return _TRANSFORM_REGISTRY[name]


def list_transform_names() -> list[str]:
    """返回所有已注册变换函数的名称列表（排序）。

    Returns:
        排序后的变换函数名称列表。
    """
    return sorted(_TRANSFORM_REGISTRY.keys())


def invert_percentile(value: float) -> float:
    """百分位反转：百分位越低（越便宜）得分越高。"""
    return 100.0 - value


def momentum_score(value: float) -> float:
    """收益率映射为动量得分（0-100）。

    精确复用旧 rotation.py 的分段线性映射逻辑。
    """
    if value > 15:
        return 95.0
    if value > 10:
        return 85.0
    if value > 5:
        return 70.0
    if value > 2:
        return 60.0
    if value > 0:
        return 50.0
    if value > -2:
        return 40.0
    if value > -5:
        return 30.0
    if value > -10:
        return 20.0
    return 10.0


def volume_score(value: float) -> float:
    """量比映射为量能得分（0-100）。

    精确复用旧 timing.py 的分段线性映射逻辑。
    """
    if value < 0.3:
        return 10.0
    if value < 0.5:
        return 20.0
    if value < 0.8:
        return 35.0
    if value < 1.0:
        return 50.0
    if value < 1.3:
        return 70.0
    if value < 1.5:
        return 80.0
    if value < 2.0:
        return 85.0
    if value < 3.0:
        return 70.0
    return 50.0


def trend_score(value: float) -> float:
    """价格相对 MA60 偏离度映射为趋势得分（0-100）。

    精确复用旧 timing.py 的线性映射逻辑。
    value 为偏离百分比：(price - ma60) / ma60 * 100。
    """
    if value <= -10:
        return 0.0
    if value >= 10:
        return 100.0
    return round(50 + value * 5, 1)


def clamp_0_100(value: float) -> float:
    """通用裁剪：限制在 0-100 范围内。"""
    return max(0.0, min(100.0, value))


def erp_score(value: float) -> float:
    """ERP 映射为得分（0-100）。

    ERP 越高表示股票相对债券越有吸引力。
    分段线性映射：>5→95, >3→80, >2→65, >1→50, >0→35, <=0→15。
    """
    if value > 5:
        return 95.0
    if value > 3:
        return 80.0
    if value > 2:
        return 65.0
    if value > 1:
        return 50.0
    if value > 0:
        return 35.0
    return 15.0


def drawdown_score(value: float) -> float:
    """回撤幅度映射为得分（0-100）。

    value 为负数（如 -12.5 表示回撤 12.5%），回撤越小得分越高。
    分段线性映射：0→100, -5→80, -10→60, -15→40, -20→20, <=-25→5。
    """
    if value >= 0:
        return 100.0
    if value >= -5:
        return 80.0 + (value + 5) / 5 * 20  # -5→80, 0→100
    if value >= -10:
        return 60.0 + (value + 10) / 5 * 20  # -10→60, -5→80
    if value >= -15:
        return 40.0 + (value + 15) / 5 * 20  # -15→40, -10→60
    if value >= -20:
        return 20.0 + (value + 20) / 5 * 20  # -20→20, -15→40
    if value >= -25:
        return 5.0 + (value + 25) / 5 * 15  # -25→5, -20→20
    return 5.0


# 内置变换函数注册（启动即注册，供配置校验与引擎执行使用）
_BUILTIN_TRANSFORMS: dict[str, Callable[[float], float]] = {
    "invert_percentile": invert_percentile,
    "momentum_score": momentum_score,
    "volume_score": volume_score,
    "trend_score": trend_score,
    "clamp_0_100": clamp_0_100,
    "erp_score": erp_score,
    "drawdown_score": drawdown_score,
}

for _name, _fn in _BUILTIN_TRANSFORMS.items():
    _TRANSFORM_REGISTRY[_name] = _fn
