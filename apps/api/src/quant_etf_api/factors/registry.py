"""FactorRegistry：因子计算器注册表，与 StrategyRegistry 同构。"""

from __future__ import annotations

from quant_etf_api.factors.base import FactorComputer, FactorSpec


class FactorRegistry:
    """因子计算器注册表，进程启动时构建一次，全局共享。

    与 plugins.registry.StrategyRegistry 结构完全对齐：
    以 factor_id 为 key 存储所有已注册的因子计算器。
    """

    def __init__(self) -> None:
        # 以 factor_id 为 key 存储所有已注册的因子计算器
        self._computers: dict[str, FactorComputer] = {}

    def register(self, computer: FactorComputer) -> None:
        """注册一个因子计算器。

        Args:
            computer: 实现 FactorComputer Protocol 的对象。
        """
        self._computers[computer.spec.factor_id] = computer

    def all(self) -> list[FactorComputer]:
        """返回所有已注册的因子计算器列表。"""
        return list(self._computers.values())

    def get(self, factor_id: str) -> FactorComputer | None:
        """按 factor_id 查找因子计算器，未找到返回 None。

        Args:
            factor_id: 因子唯一标识。
        """
        return self._computers.get(factor_id)

    def specs(self) -> list[FactorSpec]:
        """返回所有已注册因子的 FactorSpec 列表，供 API 响应和 DB 同步使用。"""
        return [c.spec for c in self._computers.values()]


def build_default_factor_registry() -> FactorRegistry:
    """构建包含所有内置因子计算器的默认注册表，进程启动时调用一次。

    所有因子均基于指数数据计算。
    ShareDeltaPctComputer（ETF 份额因子）不在默认注册表中，
    仅由 three_factor_guard 插件内部使用。

    Returns:
        已注册全部内置因子的 FactorRegistry 实例。
    """
    from quant_etf_api.factors.builtins.momentum import (
        Return20dComputer,
        Return5dComputer,
        Return60dComputer,
    )
    from quant_etf_api.factors.builtins.valuation import (
        PBPercentileComputer,
        PEPercentileComputer,
    )
    from quant_etf_api.factors.builtins.volatility import Volatility20dComputer
    from quant_etf_api.factors.builtins.volume import VolumeRatio20dComputer

    registry = FactorRegistry()
    registry.register(VolumeRatio20dComputer())
    registry.register(Return5dComputer())
    registry.register(Return20dComputer())
    registry.register(Return60dComputer())
    registry.register(Volatility20dComputer())
    registry.register(PEPercentileComputer())
    registry.register(PBPercentileComputer())
    return registry
