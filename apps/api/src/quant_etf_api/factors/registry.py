"""FactorRegistry：因子计算器注册表，进程启动时构建一次，全局共享。"""

from __future__ import annotations

from quant_etf_api.factors.base import FactorComputer, FactorSpec

# 默认回望自然日数（注册表为空或读取异常时兜底）
_DEFAULT_LOOKBACK_DAYS = 90


class FactorRegistry:
    """因子计算器注册表，以 factor_id 为 key 存储所有已注册的因子计算器。"""

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


_default_registry: FactorRegistry | None = None


def get_default_factor_registry() -> FactorRegistry:
    """返回进程级单例注册表，首次调用时构建，后续复用。

    避免 BacktestService 等每次请求重建注册表的开销。
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_factor_registry()
    return _default_registry


def build_default_factor_registry() -> FactorRegistry:
    """构建包含所有内置因子计算器的默认注册表，进程启动时调用一次。

    所有因子均基于指数数据计算。

    Returns:
        已注册全部内置因子的 FactorRegistry 实例。
    """
    from quant_etf_api.factors.builtins.erp import ERPComputer, ERPPercentileComputer
    from quant_etf_api.factors.builtins.monthly import (
        MonthlyMAComputer,
        MonthlyReturnComputer,
        MonthlyStreakComputer,
    )
    from quant_etf_api.factors.builtins.momentum import (
        Return120dComputer,
        Return17dComputer,
        Return20dComputer,
        Return5dComputer,
        Return60dComputer,
    )
    from quant_etf_api.factors.builtins.price import ChangePctComputer, ClosePriceComputer
    from quant_etf_api.factors.builtins.technical import (
        ATRComputer,
        DonchianHighComputer,
        DonchianLowComputer,
        MAComputer,
        MaxDrawdown60dComputer,
        RSIComputer,
    )
    from quant_etf_api.factors.builtins.valuation import (
        PBPercentileComputer,
        PEPercentileComputer,
    )
    from quant_etf_api.factors.builtins.volatility import (
        Volatility17dComputer,
        Volatility20dComputer,
    )
    from quant_etf_api.factors.builtins.volume import VolumeRatio17dComputer, VolumeRatio20dComputer

    registry = FactorRegistry()

    # 价格（原始字段，无需计算）
    registry.register(ClosePriceComputer())
    registry.register(ChangePctComputer())
    # 量能
    registry.register(VolumeRatio17dComputer())
    registry.register(VolumeRatio20dComputer())
    # 动量
    registry.register(Return5dComputer())
    registry.register(Return17dComputer())
    registry.register(Return20dComputer())
    registry.register(Return60dComputer())
    registry.register(Return120dComputer())
    # 波动
    registry.register(Volatility17dComputer())
    registry.register(Volatility20dComputer())
    # 估值
    registry.register(PEPercentileComputer())
    registry.register(PBPercentileComputer())
    # 技术指标 — 均线
    for period in (5, 10, 17, 20, 60):
        registry.register(MAComputer(period=period))
    # 技术指标 — ATR
    registry.register(ATRComputer(period=14))
    # 技术指标 — Donchian 通道
    registry.register(DonchianHighComputer(period=17))
    registry.register(DonchianHighComputer(period=20))
    registry.register(DonchianLowComputer(period=17))
    registry.register(DonchianLowComputer(period=20))
    # 技术指标 — RSI
    registry.register(RSIComputer(period=14))
    # 技术指标 — 最大回撤
    registry.register(MaxDrawdown60dComputer())
    # 估值 — 股权风险溢价
    registry.register(ERPComputer())
    registry.register(ERPPercentileComputer())
    # 月线级别因子
    registry.register(MonthlyMAComputer(period=5))
    registry.register(MonthlyMAComputer(period=10))
    registry.register(MonthlyReturnComputer(period=2))
    registry.register(MonthlyReturnComputer(period=3))
    registry.register(MonthlyStreakComputer())
    return registry


def max_lookback_days(registry: FactorRegistry) -> int:
    """从注册表中获取所有因子所需的最大回望自然日数。

    实时模式（FactorService._load_context）与回测模式（BacktestService）
    统一以此值为准，确保长周期因子（如 return_120d / ma_60d / 估值百分位 /
    ERP 百分位）在计算窗口内能够取到足够的回望数据。

    Args:
        registry: 因子注册表。

    Returns:
        最大回望自然日数；注册表为空或读取异常时返回默认 90。
    """
    try:
        return max(
            (c.spec.lookback_days for c in registry.all()),
            default=_DEFAULT_LOOKBACK_DAYS,
        )
    except Exception:
        return _DEFAULT_LOOKBACK_DAYS
