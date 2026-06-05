"""ETF 资产配置策略插件单元测试。

覆盖三个模块：
- timing：择时评估的边界情况
- rotation：资产轮动排名
- sizing：仓位分配逻辑
- plugin：完整决策管线集成
"""

from __future__ import annotations

from quant_etf_api.domain.strategies.models import (
    AssetRanking,
    StrategyContextData,
    TimingSignal,
)
from quant_etf_api.plugins.builtins.etf_allocation.plugin import EtfAllocationPlugin
from quant_etf_api.plugins.builtins.etf_allocation.rotation import rank_etf_assets
from quant_etf_api.plugins.builtins.etf_allocation.sizing import allocate_positions
from quant_etf_api.plugins.builtins.etf_allocation.timing import assess_timing


# ── 择时模块测试 ────────────────────────────────────────────────────────


class TestAssessTiming:
    """择时评估函数测试。"""

    def test_low_valuation_bullish_trend(self) -> None:
        """低估值 + 多头趋势 → 进攻信号。"""
        signal = assess_timing(
            pe_pct=20.0,  # 低估值得分高
            pb_pct=15.0,
            close_price=4.0,  # 高于 MA60
            ma60=3.5,
            volume_ratio=1.3,  # 温和放量
        )
        assert signal.regime == "offensive"
        assert signal.label == "进攻"
        assert signal.confidence > 50

    def test_high_valuation_bearish_trend(self) -> None:
        """高估值 + 空头趋势 → 防守信号。"""
        signal = assess_timing(
            pe_pct=85.0,  # 高估值得分低
            pb_pct=90.0,
            close_price=3.0,  # 低于 MA60
            ma60=3.5,
            volume_ratio=0.5,  # 缩量
        )
        assert signal.regime == "defensive"
        assert signal.label == "防守"
        assert signal.confidence > 50

    def test_mixed_signals_neutral(self) -> None:
        """混合信号 → 观望。"""
        signal = assess_timing(
            pe_pct=50.0,  # 中等估值
            pb_pct=50.0,
            close_price=3.5,  # 接近 MA60
            ma60=3.5,
            volume_ratio=1.0,  # 平量
        )
        assert signal.regime == "neutral"
        assert signal.label == "观望"

    def test_no_valuation_trend_data(self) -> None:
        """无估值和趋势数据，仅有量能 → 数据不足，返回中性信号。"""
        signal = assess_timing(
            pe_pct=None,
            pb_pct=None,
            close_price=None,
            ma60=None,
            volume_ratio=1.0,  # 温和放量，量能得分 70
        )
        # 估值和趋势均缺失时，仅凭量能不能给出有效信号
        assert signal.regime == "neutral"
        assert signal.factors["valuation_score"] is None
        assert signal.factors["trend_score"] is None

    def test_no_data_extreme_low_volume(self) -> None:
        """无估值/趋势 + 极端缩量 → 观望或防守。"""
        signal = assess_timing(
            pe_pct=None,
            pb_pct=None,
            close_price=None,
            ma60=None,
            volume_ratio=0.3,  # 极端缩量，量能得分 10
        )
        assert signal.regime in ("neutral", "defensive")

    def test_only_pe_data(self) -> None:
        """仅有 PE 数据时仍能输出信号。"""
        signal = assess_timing(
            pe_pct=15.0,
            pb_pct=None,
            close_price=None,
            ma60=None,
            volume_ratio=1.0,
        )
        assert signal.factors["valuation_score"] is not None
        assert signal.regime in ("offensive", "neutral", "defensive")

    def test_extreme_volume_ratio(self) -> None:
        """极端放量降低量能得分。"""
        signal_normal = assess_timing(
            pe_pct=50.0, pb_pct=50.0, close_price=3.5, ma60=3.5, volume_ratio=1.5
        )
        signal_extreme = assess_timing(
            pe_pct=50.0, pb_pct=50.0, close_price=3.5, ma60=3.5, volume_ratio=5.0
        )
        # 极端放量的量能得分应低于温和放量
        assert signal_extreme.factors["volume_score"] <= signal_normal.factors["volume_score"]


# ── 轮动模块测试 ────────────────────────────────────────────────────────


class TestRankEtfAssets:
    """资产轮动排名测试。"""

    def _make_universe(self) -> list[dict]:
        """构建测试用 ETF 宇宙。"""
        return [
            {"etf_code": "510300", "name_cn": "沪深300ETF", "category": "broad_index"},
            {"etf_code": "510500", "name_cn": "中证500ETF", "category": "broad_index"},
            {"etf_code": "159915", "name_cn": "创业板ETF", "category": "broad_index"},
        ]

    def test_momentum_dominates(self) -> None:
        """动量强的 ETF 排名靠前。"""
        universe = self._make_universe()
        etf_bars = {
            "510300": {"return_20d": 10.0, "return_5d": 3.0},
            "510500": {"return_20d": 2.0, "return_5d": 1.0},
            "159915": {"return_20d": -5.0, "return_5d": -2.0},
        }
        rankings = rank_etf_assets(universe, etf_bars, {}, {})
        # 510300 动量最强，应排第一
        assert rankings[0].etf_code == "510300"
        # 159915 动量最弱，应排最后
        assert rankings[-1].etf_code == "159915"

    def test_valuation_boosts_ranking(self) -> None:
        """低估值 + 中等动量 > 高估值 + 中等动量。"""
        universe = [
            {"etf_code": "A", "name_cn": "A", "category": "broad_index"},
            {"etf_code": "B", "name_cn": "B", "category": "broad_index"},
        ]
        etf_bars = {
            "A": {"return_20d": 5.0, "return_5d": 1.0},
            "B": {"return_20d": 5.0, "return_5d": 1.0},
        }
        # A 低估值，B 高估值
        index_valuation = {
            "idx_A": {"pe_percentile": 20.0, "pb_percentile": 15.0},
            "idx_B": {"pe_percentile": 80.0, "pb_percentile": 85.0},
        }
        etf_index_map = {"A": "idx_A", "B": "idx_B"}
        rankings = rank_etf_assets(universe, etf_bars, index_valuation, etf_index_map)
        assert rankings[0].etf_code == "A"

    def test_no_data_returns_neutral_scores(self) -> None:
        """无数据时返回中性得分。"""
        universe = self._make_universe()
        rankings = rank_etf_assets(universe, {}, {}, {})
        for r in rankings:
            assert r.score == 50.0  # 无数据时中性

    def test_empty_universe(self) -> None:
        """空宇宙返回空列表。"""
        rankings = rank_etf_assets([], {}, {}, {})
        assert rankings == []

    def test_category_mapping(self) -> None:
        """板块分类正确映射。"""
        universe = [
            {"etf_code": "510300", "name_cn": "沪深300ETF", "category": "broad_index"},
            {"etf_code": "512880", "name_cn": "证券ETF", "category": "sector"},
        ]
        rankings = rank_etf_assets(universe, {}, {}, {})
        categories = {r.etf_code: r.category for r in rankings}
        assert categories["510300"] == "宽基"
        assert categories["512880"] == "行业"


# ── 仓位分配模块测试 ────────────────────────────────────────────────────


class TestAllocatePositions:
    """仓位分配测试。"""

    def _make_rankings(self, codes: list[str], scores: list[float]) -> list[AssetRanking]:
        """构建测试用排名列表。"""
        return [
            AssetRanking(etf_code=c, name_cn=c, category="宽基", score=s)
            for c, s in zip(codes, scores)
        ]

    def test_offensive_high_exposure(self) -> None:
        """进攻信号 → 高仓位（三只加权分配，受 30% 上限裁剪）。"""
        timing = TimingSignal(regime="offensive", confidence=80, label="进攻")
        rankings = self._make_rankings(["A", "B", "C"], [80, 70, 60])
        plan = allocate_positions(timing, rankings)
        # 三只按得分加权分配 80%，A 被裁剪到 30%，总仓位略低于 0.80
        assert plan.total_exposure > 0.75
        assert plan.total_exposure <= 0.80
        assert len(plan.positions) == 3

    def test_defensive_low_exposure(self) -> None:
        """防守信号 → 低仓位。"""
        timing = TimingSignal(regime="defensive", confidence=80, label="防守")
        rankings = self._make_rankings(["A", "B", "C"], [80, 70, 60])
        plan = allocate_positions(timing, rankings)
        assert plan.total_exposure == 0.20

    def test_no_rankings_all_cash(self) -> None:
        """无排名数据 → 全仓现金。"""
        timing = TimingSignal(regime="offensive", confidence=80, label="进攻")
        plan = allocate_positions(timing, None)
        assert plan.total_exposure == 0.0
        assert plan.cash_ratio == 1.0

    def test_no_timing_default_neutral(self) -> None:
        """无择时信号 → 默认中性 50%。"""
        rankings = self._make_rankings(["A", "B"], [80, 70])
        plan = allocate_positions(None, rankings)
        assert plan.total_exposure == 0.50

    def test_single_position_cap(self) -> None:
        """单只 ETF 仓位不超过 30%。"""
        timing = TimingSignal(regime="offensive", confidence=80, label="进攻")
        # 只有一只 ETF，得分很高
        rankings = self._make_rankings(["A"], [100])
        plan = allocate_positions(timing, rankings)
        assert plan.positions["A"] <= 0.30

    def test_max_positions_parameter(self) -> None:
        """max_positions 参数限制持仓数量。"""
        timing = TimingSignal(regime="offensive", confidence=80, label="进攻")
        rankings = self._make_rankings(["A", "B", "C", "D", "E", "F"], [90, 80, 70, 60, 50, 40])
        plan = allocate_positions(timing, rankings, params={"max_positions": 3})
        assert len(plan.positions) == 3
        assert "A" in plan.positions
        assert "B" in plan.positions
        assert "C" in plan.positions

    def test_cash_ratio_sums_to_one(self) -> None:
        """仓位 + 现金 = 1。"""
        timing = TimingSignal(regime="neutral", confidence=50, label="观望")
        rankings = self._make_rankings(["A", "B", "C"], [80, 70, 60])
        plan = allocate_positions(timing, rankings)
        assert abs(plan.total_exposure + plan.cash_ratio - 1.0) < 0.01


# ── 插件集成测试 ────────────────────────────────────────────────────────


class TestEtfAllocationPlugin:
    """EtfAllocationPlugin 集成测试。"""

    def _make_context(self) -> StrategyContextData:
        """构建测试用上下文。"""
        return StrategyContextData(
            benchmark_changes={"000300": 0.5},
            extra={
                "asset_bars": {
                    "510300": {
                        "close_price": 4.0,
                        "ma60": 3.8,
                        "volume_ratio_20d": 1.3,
                        "return_20d": 5.0,
                        "return_5d": 2.0,
                        "change_pct": 0.5,
                        "etf_5d_return": 2.0,
                    },
                    "510500": {
                        "close_price": 6.0,
                        "ma60": 6.2,
                        "volume_ratio_20d": 0.8,
                        "return_20d": -3.0,
                        "return_5d": -1.0,
                        "change_pct": -0.3,
                        "etf_5d_return": -1.0,
                    },
                },
                "index_valuation": {
                    "000300": {"pe_percentile": 30.0, "pb_percentile": 25.0},
                },
                "asset_index_map": {"510300": "000300", "510500": "000905"},
                "index_5d_return": {"000300": 1.0},
            },
        )

    def _make_universe(self) -> list[dict]:
        """构建测试用 ETF 宇宙。"""
        return [
            {"etf_code": "510300", "name_cn": "沪深300ETF", "category": "broad_index"},
            {"etf_code": "510500", "name_cn": "中证500ETF", "category": "broad_index"},
        ]

    def test_plugin_metadata(self) -> None:
        """插件元数据完整。"""
        plugin = EtfAllocationPlugin()
        assert plugin.strategy_id == "etf_allocation"
        assert plugin.display_name == "ETF 资产配置"
        assert plugin.version == "1.0.0"

    def test_assess_market_timing(self) -> None:
        """择时评估返回有效信号。"""
        plugin = EtfAllocationPlugin()
        context = self._make_context()
        signal = plugin.assess_market_timing(
            trade_date=None,  # type: ignore[arg-type]
            context=context,
        )
        assert signal.regime in ("offensive", "neutral", "defensive")
        assert 0 <= signal.confidence <= 100

    def test_rank_assets(self) -> None:
        """资产排名返回有序列表。"""
        plugin = EtfAllocationPlugin()
        context = self._make_context()
        universe = self._make_universe()
        rankings = plugin.rank_assets(
            trade_date=None,  # type: ignore[arg-type]
            universe=universe,
            context=context,
        )
        assert len(rankings) == 2
        # 510300 动量更强，应排第一
        assert rankings[0].etf_code == "510300"

    def test_allocate_positions(self) -> None:
        """仓位分配返回有效方案。"""
        plugin = EtfAllocationPlugin()
        timing = TimingSignal(regime="offensive", confidence=80, label="进攻")
        rankings = [
            AssetRanking(etf_code="510300", name_cn="沪深300ETF", category="宽基", score=80),
            AssetRanking(etf_code="510500", name_cn="中证500ETF", category="宽基", score=60),
        ]
        plan = plugin.allocate_positions(timing, rankings)
        assert plan.total_exposure > 0
        assert len(plan.positions) > 0

    def test_run_for_universe_compat(self) -> None:
        """兼容旧模式，返回 StrategyResult 列表。"""
        plugin = EtfAllocationPlugin()
        context = self._make_context()
        universe = self._make_universe()
        from datetime import date

        results = plugin.run_for_universe(
            trade_date=date(2025, 1, 15),
            universe=universe,
            context=context,
        )
        assert len(results) == 2
        for r in results:
            assert r.strategy_id == "etf_allocation"
            assert 0 <= r.signal_score <= 100
            assert r.signal_level in ("HIGH", "MID", "LOW")

    def test_decision_pipeline_integration(self) -> None:
        """完整决策管线集成：择时 → 排名 → 分配。"""
        plugin = EtfAllocationPlugin()
        context = self._make_context()
        universe = self._make_universe()
        from datetime import date

        trade_date = date(2025, 1, 15)
        timing = plugin.assess_market_timing(trade_date, context)
        rankings = plugin.rank_assets(trade_date, universe, context)
        plan = plugin.allocate_positions(timing, rankings)

        # 验证管线完整性
        assert plan.total_exposure + plan.cash_ratio <= 1.01  # 允许浮点误差
        assert plan.reasoning != ""

    def test_has_decision_pipeline(self) -> None:
        """插件支持决策管线检查。"""
        plugin = EtfAllocationPlugin()
        assert hasattr(plugin, "assess_market_timing")
        assert hasattr(plugin, "rank_assets")
        assert hasattr(plugin, "allocate_positions")
