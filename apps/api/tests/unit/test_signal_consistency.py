"""B5 实时与回测信号口径一致性单元测试。

覆盖：
- 同一交易日、同一策略配置下，实时 `_build_strategy_results` 与回测
  `_write_index_results` 的信号等级、signal_score、target_weight 完全一致；
- 防守 regime 下回测同样传入 timing_regime，全部信号降为 LOW。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    ScoreConfig,
    StrategyConfig,
    TimingConfig,
    TimingThresholds,
)
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.services.backtest_service import BacktestService


def _make_context(
    asset_factors: dict[tuple[str, str], float | None],
    market_factors: dict[str, float | None] | None = None,
) -> EngineContext:
    """构建测试用上下文（指数代码与回测 index_code 对齐）。"""
    codes = ["000300", "000905", "000016"]
    return EngineContext(
        trade_date=date(2025, 1, 15),
        universe=[{"index_code": c, "name_cn": c, "category": "broad_index"} for c in codes],
        asset_factors=asset_factors,
        market_factors=market_factors or {},
        asset_metadata={c: {"name_cn": c, "category": "broad_index"} for c in codes},
    )


def _make_config(
    strategy_id: str = "test_consistency",
    timing: TimingConfig | None = None,
) -> StrategyConfig:
    """构建配置模式策略（回测要求必须配置 portfolio 模块）。"""
    return StrategyConfig(
        strategy_id=strategy_id,
        display_name="一致性测试策略",
        timing=timing,
        score=ScoreConfig(factors={"momentum": 0.6, "valuation": 0.4}),
        rank=RankConfig(sort_by="score", order="desc", top_n=3),
        portfolio=PortfolioConfig(
            method="equal_weight",
            timing_exposure={"offensive": 0.80, "neutral": 0.50, "defensive": 0.20},
        ),
    )


def _run_backtest_write(
    svc: BacktestService,
    result: object,
    config: StrategyConfig,
    trade_date: date,
    codes: list[str],
    selection_rebalanced: bool = True,
) -> list[object]:
    """模拟回测 _write_index_results 写入并捕获生成的 ORM 行。"""
    mock_add = MagicMock()
    svc._backtest_repo.add_index_result = mock_add
    universe = [{"index_code": c} for c in codes]
    svc._write_index_results(
        backtest_id="bt-consistency",
        trade_date=trade_date,
        next_date=None,
        universe=universe,
        result=result,
        all_bars={},
        signal_positions=result.positions if result.positions else {},
        timing_regime=result.timing.regime if result.timing else None,
        scoring_mode=config.score.scoring_mode,
        selection_rebalanced=selection_rebalanced,
    )
    return [call.args[0] for call in mock_add.call_args_list]


class TestSignalConsistency:
    """实时与回测信号口径一致性。"""

    def test_levels_and_scores_match_without_timing(self) -> None:
        """无择时配置下，实时与回测的等级/得分/目标权重完全一致。"""
        engine = StrategyEngine()
        config = _make_config()
        context = _make_context(
            asset_factors={
                ("000300", "momentum"): 80.0,
                ("000300", "valuation"): 60.0,
                ("000905", "momentum"): 70.0,
                ("000905", "valuation"): 50.0,
                ("000016", "momentum"): 60.0,
                ("000016", "valuation"): 40.0,
            },
        )

        result = engine.run(config, context, include_details=True)
        realtime = {
            r.index_code: (r.signal_score, r.signal_level, r.payload["target_weight"])
            for r in result.strategy_results
        }

        svc = BacktestService(db=MagicMock())
        codes = ["000300", "000905", "000016"]
        rows = _run_backtest_write(svc, result, config, context.trade_date, codes)

        assert len(rows) == len(realtime)
        for row in rows:
            score, level, weight = realtime[row.index_code]
            assert row.signal_score == score
            assert row.signal_level == level
            assert row.target_weight == round(weight, 4)
            assert row.in_portfolio == (weight > 0)

    def test_defensive_regime_forces_low_in_backtest(self) -> None:
        """防守 regime 下回测传入 timing_regime，与实时一样全部 LOW。"""
        engine = StrategyEngine()
        config = _make_config(
            timing=TimingConfig(
                factors={"pe_percentile": 0.5, "pb_percentile": 0.5},
                transforms={
                    "pe_percentile": "invert_percentile",
                    "pb_percentile": "invert_percentile",
                },
                thresholds=TimingThresholds(offensive=65, defensive=35),
            )
        )
        context = _make_context(
            asset_factors={
                ("000300", "momentum"): 80.0,
                ("000300", "valuation"): 60.0,
                ("000905", "momentum"): 70.0,
                ("000905", "valuation"): 50.0,
                ("000016", "momentum"): 60.0,
                ("000016", "valuation"): 40.0,
            },
            market_factors={"pe_percentile": 85.0, "pb_percentile": 90.0},
        )

        result = engine.run(config, context, include_details=True)
        assert result.timing is not None
        assert result.timing.regime == "defensive"

        realtime_levels = {r.index_code: r.signal_level for r in result.strategy_results}
        svc = BacktestService(db=MagicMock())
        codes = ["000300", "000905", "000016"]
        rows = _run_backtest_write(svc, result, config, context.trade_date, codes)

        for row in rows:
            assert row.signal_level == realtime_levels[row.index_code] == "LOW"


class TestScoredFlag:
    """落库必须区分"未参与评分"与"得分为 0"，否则组合分数 IC 会被占位 0 污染。"""

    def test_assets_outside_engine_scores_are_marked_unscored(self) -> None:
        """universe 比引擎评分集合多出的资产记为 scored=False、得分占位 0.0。"""
        engine = StrategyEngine()
        config = _make_config()
        context = _make_context(
            asset_factors={
                ("000300", "momentum"): 80.0,
                ("000300", "valuation"): 60.0,
                ("000905", "momentum"): 70.0,
                ("000905", "valuation"): 50.0,
                ("000016", "momentum"): 60.0,
                ("000016", "valuation"): 40.0,
            },
        )
        result = engine.run(config, context, include_details=True)
        svc = BacktestService(db=MagicMock())
        # 399001 不在上下文中：等价于被候选池剔除或被过滤规则拒绝的资产
        codes = ["000300", "000905", "000016", "399001"]
        rows = _run_backtest_write(svc, result, config, context.trade_date, codes)

        flags = {row.index_code: (row.scored, row.signal_score) for row in rows}
        assert flags["399001"] == (False, 0.0)
        for code in ("000300", "000905", "000016"):
            assert flags[code][0] is True, f"{code} 应参与评分"
            assert flags[code][1] > 0

    def test_selection_rebalance_flag_is_persisted(self) -> None:
        """周/月策略的非选股调仓日不得作为组合分数 IC 观测。"""
        engine = StrategyEngine()
        config = _make_config()
        context = _make_context(
            asset_factors={
                ("000300", "momentum"): 80.0,
                ("000300", "valuation"): 60.0,
                ("000905", "momentum"): 70.0,
                ("000905", "valuation"): 50.0,
                ("000016", "momentum"): 60.0,
                ("000016", "valuation"): 40.0,
            }
        )
        result = engine.run(config, context, include_details=True)
        rows = _run_backtest_write(
            BacktestService(db=MagicMock()),
            result,
            config,
            context.trade_date,
            ["000300", "000905", "000016"],
            selection_rebalanced=False,
        )
        assert all(row.selection_rebalanced is False for row in rows)
