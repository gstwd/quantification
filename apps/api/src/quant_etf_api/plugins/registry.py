"""策略注册表（已废弃）。

策略管理已迁移至 StrategyConfigService + strategy_config 表。
此模块仅保留空壳以避免导入错误。
"""

from __future__ import annotations

from typing import Any


class StrategyRegistry:
    """空壳注册表，所有方法返回空结果。"""

    def register(self, plugin: Any) -> None:
        """不再支持注册。"""

    def all(self) -> list[Any]:
        """返回空列表。"""
        return []

    def get(self, strategy_id: str) -> Any:
        """始终返回 None。"""
        return None

    def has_decision_pipeline(self, strategy_id: str) -> bool:
        """始终返回 False。"""
        return False

    def as_summaries(self) -> list[dict[str, Any]]:
        """返回空列表。"""
        return []


def build_default_registry() -> StrategyRegistry:
    """构建空注册表。"""
    return StrategyRegistry()
