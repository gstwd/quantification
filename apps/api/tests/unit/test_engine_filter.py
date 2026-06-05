"""过滤模块单元测试。

覆盖 DefaultFilterEngine 的核心逻辑：
- 各操作符过滤
- AND/OR 逻辑组合
- 边界情况
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import FilterConfig, FilterRule
from quant_etf_api.engine.filter import DefaultFilterEngine


def _make_context(
    asset_factors: dict[tuple[str, str], float | None] | None = None,
    universe: list[dict] | None = None,
) -> EngineContext:
    """构建测试用上下文。"""
    return EngineContext(
        trade_date=date(2025, 1, 15),
        universe=universe or [
            {"etf_code": "A", "name_cn": "A", "category": "broad_index"},
            {"etf_code": "B", "name_cn": "B", "category": "broad_index"},
            {"etf_code": "C", "name_cn": "C", "category": "broad_index"},
        ],
        asset_factors=asset_factors or {},
    )


class TestDefaultFilterEngine:
    """默认过滤引擎测试。"""

    def test_gt_filter(self) -> None:
        """大于过滤。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[FilterRule(factor="momentum", op="gt", value=50.0)],
        )
        assets = {"A": 80.0, "B": 50.0, "C": 30.0}
        context = _make_context(
            asset_factors={
                ("A", "momentum"): 60.0,
                ("B", "momentum"): 50.0,  # 不满足 > 50
                ("C", "momentum"): 30.0,
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" not in result
        assert "C" not in result

    def test_lt_filter(self) -> None:
        """小于过滤。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[FilterRule(factor="pe_percentile", op="lt", value=70.0)],
        )
        assets = {"A": 80.0, "B": 60.0, "C": 70.0}
        context = _make_context(
            asset_factors={
                ("A", "pe_percentile"): 50.0,
                ("B", "pe_percentile"): 60.0,
                ("C", "pe_percentile"): 70.0,  # 不满足 < 70
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" in result
        assert "C" not in result

    def test_between_filter(self) -> None:
        """区间过滤。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[FilterRule(factor="volume", op="between", value=[0.8, 1.5])],
        )
        assets = {"A": 80.0, "B": 60.0, "C": 40.0}
        context = _make_context(
            asset_factors={
                ("A", "volume"): 1.0,
                ("B", "volume"): 0.5,  # 不在区间
                ("C", "volume"): 2.0,  # 不在区间
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" not in result
        assert "C" not in result

    def test_and_logic(self) -> None:
        """AND 逻辑：所有规则必须满足。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[
                FilterRule(factor="a", op="gt", value=50.0),
                FilterRule(factor="b", op="gt", value=50.0),
            ],
        )
        assets = {"A": 80.0, "B": 60.0, "C": 40.0}
        context = _make_context(
            asset_factors={
                ("A", "a"): 60.0, ("A", "b"): 60.0,  # 两个都满足
                ("B", "a"): 60.0, ("B", "b"): 40.0,  # 只满足一个
                ("C", "a"): 40.0, ("C", "b"): 40.0,  # 都不满足
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" not in result
        assert "C" not in result

    def test_or_logic(self) -> None:
        """OR 逻辑：任一规则满足即可。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="OR",
            rules=[
                FilterRule(factor="a", op="gt", value=80.0),
                FilterRule(factor="b", op="gt", value=80.0),
            ],
        )
        assets = {"A": 80.0, "B": 60.0, "C": 40.0}
        context = _make_context(
            asset_factors={
                ("A", "a"): 90.0, ("A", "b"): 40.0,  # 只满足第一个
                ("B", "a"): 40.0, ("B", "b"): 90.0,  # 只满足第二个
                ("C", "a"): 40.0, ("C", "b"): 40.0,  # 都不满足
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" in result
        assert "C" not in result

    def test_no_rules_returns_all(self) -> None:
        """无规则时返回所有资产。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(logic="AND", rules=[])
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context()

        result = engine.filter(config, assets, context)

        assert result == assets

    def test_missing_factor_value_filtered_out(self) -> None:
        """因子值缺失时被过滤掉。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[FilterRule(factor="momentum", op="gt", value=50.0)],
        )
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "momentum"): 60.0,
                # B 没有 momentum 因子值
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" not in result
