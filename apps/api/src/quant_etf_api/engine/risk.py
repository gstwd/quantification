"""风控模块：对仓位施加约束限制。

支持单资产仓位上限、组合总仓位上限、最低现金比例。
"""

from __future__ import annotations

import logging
from typing import Protocol

from quant_etf_api.engine.config import RiskConfig

logger = logging.getLogger(__name__)


class RiskManager(Protocol):
    """风控管理器协议。"""

    def apply_constraints(
        self, config: RiskConfig, positions: dict[str, float]
    ) -> dict[str, float]:
        """应用风控约束，裁剪超限仓位。

        Args:
            config: 风控配置。
            positions: 原始目标仓位。

        Returns:
            裁剪后的目标仓位。
        """
        ...


class DefaultRiskManager:
    """默认风控管理器。"""

    def apply_constraints(
        self, config: RiskConfig, positions: dict[str, float]
    ) -> dict[str, float]:
        """按顺序应用风控约束。"""
        if not positions:
            return positions

        # 1. 单资产仓位上限裁剪
        clipped: dict[str, float] = {}
        for code, weight in positions.items():
            clipped[code] = min(weight, config.max_asset_weight)

        # 2. 组合总仓位上限
        total = sum(clipped.values())
        if total > config.max_portfolio_exposure:
            scale = config.max_portfolio_exposure / total
            clipped = {k: round(v * scale, 4) for k, v in clipped.items()}
            total = config.max_portfolio_exposure

        # 3. 最低现金比例
        min_exposure = 1.0 - config.min_cash_ratio
        if total > min_exposure and total > 0:
            scale = min_exposure / total
            clipped = {k: round(v * scale, 4) for k, v in clipped.items()}

        return clipped
