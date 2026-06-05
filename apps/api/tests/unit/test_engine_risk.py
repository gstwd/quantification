"""风控模块单元测试。

覆盖 DefaultRiskManager 的核心逻辑：
- 单资产仓位上限
- 组合总仓位上限
- 最低现金比例
"""

from __future__ import annotations

from quant_etf_api.engine.config import RiskConfig
from quant_etf_api.engine.risk import DefaultRiskManager


class TestDefaultRiskManager:
    """默认风控管理器测试。"""

    def test_max_asset_weight(self) -> None:
        """单资产仓位上限裁剪。"""
        manager = DefaultRiskManager()
        config = RiskConfig(max_asset_weight=0.30)
        positions = {"A": 0.50, "B": 0.30}

        result = manager.apply_constraints(config, positions)

        assert result["A"] == 0.30  # 被裁剪到 30%
        assert result["B"] == 0.30  # 保持不变

    def test_max_portfolio_exposure(self) -> None:
        """组合总仓位上限。"""
        manager = DefaultRiskManager()
        config = RiskConfig(max_asset_weight=1.0, max_portfolio_exposure=0.80)
        positions = {"A": 0.50, "B": 0.50}

        result = manager.apply_constraints(config, positions)

        total = sum(result.values())
        assert abs(total - 0.80) < 0.01

    def test_min_cash_ratio(self) -> None:
        """最低现金比例。"""
        manager = DefaultRiskManager()
        config = RiskConfig(
            max_asset_weight=1.0,
            max_portfolio_exposure=1.0,
            min_cash_ratio=0.20,
        )
        positions = {"A": 0.50, "B": 0.50}

        result = manager.apply_constraints(config, positions)

        total = sum(result.values())
        assert total <= 0.80  # 至少 20% 现金

    def test_combined_constraints(self) -> None:
        """组合约束：单资产上限 + 组合上限 + 现金比例。"""
        manager = DefaultRiskManager()
        config = RiskConfig(
            max_asset_weight=0.30,
            max_portfolio_exposure=0.90,
            min_cash_ratio=0.10,
        )
        positions = {"A": 0.50, "B": 0.40, "C": 0.30}

        result = manager.apply_constraints(config, positions)

        # A 被裁剪到 30%
        assert result["A"] == 0.30
        # 总仓位不超过 90%
        total = sum(result.values())
        assert total <= 0.90
        # 现金至少 10%
        assert total <= 0.90

    def test_empty_positions(self) -> None:
        """空仓位返回空。"""
        manager = DefaultRiskManager()
        config = RiskConfig()

        result = manager.apply_constraints(config, {})

        assert result == {}

    def test_no_constraints(self) -> None:
        """无约束时仓位不变。"""
        manager = DefaultRiskManager()
        config = RiskConfig(
            max_asset_weight=1.0,
            max_portfolio_exposure=1.0,
            min_cash_ratio=0.0,
        )
        positions = {"A": 0.50, "B": 0.30}

        result = manager.apply_constraints(config, positions)

        assert result == positions
