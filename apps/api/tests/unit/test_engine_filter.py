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
        universe=universe
        or [
            {"index_code": "A", "name_cn": "A", "category": "broad_index"},
            {"index_code": "B", "name_cn": "B", "category": "broad_index"},
            {"index_code": "C", "name_cn": "C", "category": "broad_index"},
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
                ("A", "a"): 60.0,
                ("A", "b"): 60.0,  # 两个都满足
                ("B", "a"): 60.0,
                ("B", "b"): 40.0,  # 只满足一个
                ("C", "a"): 40.0,
                ("C", "b"): 40.0,  # 都不满足
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
                ("A", "a"): 90.0,
                ("A", "b"): 40.0,  # 只满足第一个
                ("B", "a"): 40.0,
                ("B", "b"): 90.0,  # 只满足第二个
                ("C", "a"): 40.0,
                ("C", "b"): 40.0,  # 都不满足
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

    def test_cross_factor_gt(self) -> None:
        """跨因子比较：MA5 > MA20 金叉判定。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[FilterRule(factor="ma_5d", op="gt", compare_to="ma_20d")],
        )
        assets = {"A": 80.0, "B": 60.0, "C": 40.0}
        context = _make_context(
            asset_factors={
                ("A", "ma_5d"): 5100.0,
                ("A", "ma_20d"): 5050.0,  # 金叉
                ("B", "ma_5d"): 5000.0,
                ("B", "ma_20d"): 5050.0,  # 死叉
                ("C", "ma_5d"): 5000.0,
                ("C", "ma_20d"): 5000.0,  # 相等，不满足 gt
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" not in result
        assert "C" not in result

    def test_cross_factor_missing_value(self) -> None:
        """跨因子比较：compare_to 因子值缺失时被过滤掉。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[FilterRule(factor="ma_5d", op="gt", compare_to="ma_20d")],
        )
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "ma_5d"): 5100.0,
                ("A", "ma_20d"): 5050.0,  # 正常
                ("B", "ma_5d"): 5100.0,  # B 缺少 ma_20d
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" not in result

    def test_cross_factor_with_and_logic(self) -> None:
        """跨因子比较 + 固定阈值 AND 组合。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[
                FilterRule(factor="ma_5d", op="gt", compare_to="ma_20d"),  # 金叉
                FilterRule(factor="volume_ratio_20d", op="gte", value=0.8),  # 量比正常
            ],
        )
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "ma_5d"): 5100.0,
                ("A", "ma_20d"): 5050.0,
                ("A", "volume_ratio_20d"): 1.2,  # 两个都满足
                ("B", "ma_5d"): 5100.0,
                ("B", "ma_20d"): 5050.0,
                ("B", "volume_ratio_20d"): 0.5,  # 量比不满足
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" not in result

    def test_cross_factor_neq(self) -> None:
        """跨因子比较：neq 操作符排除相等资产。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[FilterRule(factor="return_5d", op="neq", compare_to="return_20d")],
        )
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "return_5d"): 3.5,
                ("A", "return_20d"): 8.2,  # 不相等，通过
                ("B", "return_5d"): 5.0,
                ("B", "return_20d"): 5.0,  # 相等，不满足 neq
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" not in result


class TestFilterMissingStrategy:
    """B6：过滤规则因子缺失时的 missing_strategy 行为。"""

    def test_default_is_fail(self) -> None:
        """默认 missing_strategy=fail，缺失资产被过滤，与历史行为一致。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[FilterRule(factor="pe_percentile", op="gt", value=30.0)],
        )
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "pe_percentile"): 50.0,
                # B 无估值因子值
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" not in result

    def test_missing_strategy_pass_keeps_asset(self) -> None:
        """missing_strategy=pass：因子缺失时规则视为通过，资产保留。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[
                FilterRule(
                    factor="pe_percentile",
                    op="gt",
                    value=30.0,
                    missing_strategy="pass",
                )
            ],
        )
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "pe_percentile"): 50.0,
                # B 无估值因子值，按 pass 处理
            },
        )

        result = engine.filter(config, assets, context)

        assert result == assets

    def test_missing_strategy_exclude_marks_debug(self) -> None:
        """missing_strategy=exclude：缺失资产被排除，且调试明细标记 missing。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[
                FilterRule(
                    factor="pe_percentile",
                    op="gt",
                    value=30.0,
                    missing_strategy="exclude",
                )
            ],
        )
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "pe_percentile"): 50.0,
                # B 无估值因子值，按 exclude 排除
            },
        )
        debug: list = []

        result = engine.filter(config, assets, context, debug=debug)

        assert "A" in result
        assert "B" not in result
        by_code = {d.index_code: d for d in debug}
        assert by_code["B"].passed is False
        assert by_code["B"].fail_reason == "pe_percentile 因子缺失(exclude)"
        assert by_code["B"].rule_results[0].missing is True
        assert by_code["B"].rule_results[0].missing_strategy == "exclude"

    def test_missing_strategy_pass_with_or_logic(self) -> None:
        """OR 逻辑下 pass 规则缺失时视为满足，资产可被保留。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="OR",
            rules=[
                FilterRule(factor="a", op="gt", value=80.0),
                FilterRule(factor="b", op="gt", value=80.0, missing_strategy="pass"),
            ],
        )
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "a"): 40.0,
                ("A", "b"): 40.0,  # 两条都不满足
                ("B", "a"): 40.0,  # b 缺失但 missing_strategy=pass → 满足
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" not in result
        assert "B" in result

    def test_cross_factor_missing_with_pass(self) -> None:
        """跨因子比较：compare_to 参照值缺失时按 missing_strategy 处理。"""
        engine = DefaultFilterEngine()
        config = FilterConfig(
            logic="AND",
            rules=[
                FilterRule(
                    factor="ma_5d",
                    op="gt",
                    compare_to="ma_20d",
                    missing_strategy="pass",
                )
            ],
        )
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "ma_5d"): 5100.0,
                ("A", "ma_20d"): 5050.0,  # 正常金叉
                ("B", "ma_5d"): 5100.0,  # B 缺少 ma_20d → pass
            },
        )

        result = engine.filter(config, assets, context)

        assert "A" in result
        assert "B" in result
