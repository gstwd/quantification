"""策略插件基础设施：领域模型 re-export。

策略执行已迁移至 engine/ 包（组件化 + 配置驱动架构）。
此模块保留 StrategyContextData、StrategyResult 等领域的 re-export，
供旧代码渐进迁移使用。
"""

from __future__ import annotations

from quant_etf_api.domain.strategies.models import (
    AllocationPlan,
    AssetRanking,
    StrategyContextData,
    StrategyResult,
    TimingSignal,
)

__all__ = [
    "AllocationPlan",
    "AssetRanking",
    "StrategyContextData",
    "StrategyResult",
    "TimingSignal",
]
