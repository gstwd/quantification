"""排名模块单元测试。

覆盖 DefaultRankEngine 的核心逻辑：
- 排序方向
- TopN/BottomN 截取
- 子维度排名
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import RankConfig
from quant_etf_api.engine.rank import DefaultRankEngine


def _make_context(
    asset_factors: dict[tuple[str, str], float | None] | None = None,
    asset_metadata: dict[str, dict] | None = None,
) -> EngineContext:
    """构建测试用上下文。"""
    return EngineContext(
        trade_date=date(2025, 1, 15),
        universe=[],
        asset_factors=asset_factors or {},
        asset_metadata=asset_metadata or {},
    )


class TestDefaultRankEngine:
    """默认排名引擎测试。"""

    def test_descending_sort(self) -> None:
        """降序排序。"""
        engine = DefaultRankEngine()
        config = RankConfig(sort_by="score", order="desc")
        assets = {"A": 80.0, "B": 60.0, "C": 90.0}
        context = _make_context()

        rankings = engine.rank(config, assets, context)

        assert rankings[0].index_code == "C"
        assert rankings[1].index_code == "A"
        assert rankings[2].index_code == "B"

    def test_ascending_sort(self) -> None:
        """升序排序。"""
        engine = DefaultRankEngine()
        config = RankConfig(sort_by="score", order="asc")
        assets = {"A": 80.0, "B": 60.0, "C": 90.0}
        context = _make_context()

        rankings = engine.rank(config, assets, context)

        assert rankings[0].index_code == "B"
        assert rankings[1].index_code == "A"
        assert rankings[2].index_code == "C"

    def test_top_n(self) -> None:
        """TopN 截取。"""
        engine = DefaultRankEngine()
        config = RankConfig(sort_by="score", order="desc", top_n=2)
        assets = {"A": 80.0, "B": 60.0, "C": 90.0}
        context = _make_context()

        rankings = engine.rank(config, assets, context)

        assert len(rankings) == 2
        assert rankings[0].index_code == "C"
        assert rankings[1].index_code == "A"

    def test_bottom_n(self) -> None:
        """BottomN 截取。"""
        engine = DefaultRankEngine()
        config = RankConfig(sort_by="score", order="desc", bottom_n=2)
        assets = {"A": 80.0, "B": 60.0, "C": 90.0}
        context = _make_context()

        rankings = engine.rank(config, assets, context)

        assert len(rankings) == 2
        assert rankings[0].index_code == "A"
        assert rankings[1].index_code == "B"

    def test_empty_assets(self) -> None:
        """空资产返回空列表。"""
        engine = DefaultRankEngine()
        config = RankConfig()
        context = _make_context()

        rankings = engine.rank(config, {}, context)

        assert rankings == []

    def test_category_mapping(self) -> None:
        """板块分类正确映射。"""
        engine = DefaultRankEngine()
        config = RankConfig()
        assets = {"A": 80.0, "B": 60.0}
        context = _make_context(
            asset_metadata={
                "A": {"name_cn": "沪深300", "category": "broad_index"},
                "B": {"name_cn": "证券", "category": "sector"},
            },
        )

        rankings = engine.rank(config, assets, context)

        categories = {r.index_code: r.category for r in rankings}
        assert categories["A"] == "宽基"
        assert categories["B"] == "行业"

    def test_sub_rankings(self) -> None:
        """子维度排名分配。"""
        engine = DefaultRankEngine()
        config = RankConfig()
        assets = {"A": 80.0, "B": 70.0, "C": 60.0}
        context = _make_context(
            asset_factors={
                ("A", "return_20d"): 10.0,
                ("B", "return_20d"): 5.0,
                ("C", "return_20d"): 15.0,
                ("A", "pe_percentile"): 30.0,
                ("B", "pe_percentile"): 60.0,
                ("C", "pe_percentile"): 45.0,
            },
        )

        rankings = engine.rank(config, assets, context)

        # 动量排名：C(15) > A(10) > B(5)
        rank_map = {r.index_code: r for r in rankings}
        assert rank_map["C"].momentum_rank == 1
        assert rank_map["A"].momentum_rank == 2
        assert rank_map["B"].momentum_rank == 3

        # 估值排名（吸引力 = 100 - pe_percentile）：A(70) > C(55) > B(40)
        assert rank_map["A"].valuation_rank == 1
        assert rank_map["C"].valuation_rank == 2
        assert rank_map["B"].valuation_rank == 3
