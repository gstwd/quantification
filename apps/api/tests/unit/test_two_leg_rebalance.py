"""双腿调仓（选股腿 / 风险腿）与仓位缩放单元测试。

覆盖：
- ``compose_two_leg_positions`` 的合成口径：两腿同日 = 改造前的"新成分 + 择时仓位"；
  仅选股腿 = 只换成分、维持当前总仓位；仅风险腿 = 等比缩放现有成分；皆否 = 维持持仓；
- ``scale_to_exposure`` 的守恒性与和差校正（不越过目标仓位）；
- 回测主循环层面的双腿调度：同频两腿逐日等价于改造前的单腿行为；风险腿日照常缩放
  并计入换手，选股腿日只替换成分。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from quant_etf_api.domain.portfolio.scaling import (
    compose_two_leg_positions,
    scale_to_exposure,
)
from quant_etf_api.domain.strategies.rebalance import DefaultRebalanceScheduler
from quant_etf_api.domain.strategies.models import TimingSignal
from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RebalanceConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService

DATES = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]


class _WeekdayCalendar:
    """仅按周末判定交易日的日历替身（覆盖测试区间内的周一至周五）。"""

    def is_trading_day(self, day: date) -> bool:
        """按周末判定。"""
        return day.weekday() < 5


class TestScaleToExposure:
    """等比缩放的守恒性与边界。"""

    def test_scales_proportionally(self) -> None:
        """保持相对权重，只调整总量。"""
        scaled = scale_to_exposure({"a": 0.4, "b": 0.2}, 0.3)
        assert scaled == {"a": 0.2, "b": 0.1}

    def test_zero_target_liquidates(self) -> None:
        """目标仓位为 0 时清仓。"""
        assert scale_to_exposure({"a": 0.5}, 0.0) == {}

    def test_empty_positions_are_not_opened(self) -> None:
        """空持仓不建仓（风险腿不选股）。"""
        assert scale_to_exposure({}, 0.8) == {}

    def test_non_positive_weights_are_dropped(self) -> None:
        """非正权重不参与缩放。"""
        assert scale_to_exposure({"a": 0.5, "b": 0.0, "c": -0.1}, 0.5) == {"a": 0.5}

    def test_repeated_scaling_is_stable(self) -> None:
        """对同一目标反复缩放不产生暴露漂移（四舍五入不累积）。"""
        positions = {"a": 0.3333, "b": 0.3333, "c": 0.3334}
        target = 0.5
        current = scale_to_exposure(positions, target)
        for _ in range(20):
            current = scale_to_exposure(current, target)
        # 单腿 4 位小数的口径下随机误差不超过腿数 × 1e-4，且不超过目标仓位
        assert 0.5 - 0.001 <= sum(current.values()) <= target
        # 反复缩放不改变结果（幂等）
        assert current == scale_to_exposure(current, target)
        # 相对权重保持不变
        assert current == {"a": 0.1666, "b": 0.1666, "c": 0.1667}

    def test_single_asset_is_exact(self) -> None:
        """单资产时缩放结果精确等于目标仓位（无多腿舍入噪声）。"""
        current = {"a": 0.8}
        for _ in range(20):
            current = scale_to_exposure(current, 0.35)
        assert current == {"a": 0.35}

    def test_rounding_never_exceeds_target(self) -> None:
        """多腿四舍五入后总量不超过目标仓位（风控上限不被突破）。"""
        positions = {f"c{i}": 1.0 for i in range(7)}
        scaled = scale_to_exposure(positions, 0.5)
        assert sum(scaled.values()) <= 0.5


class TestComposeTwoLegPositions:
    """双腿合成的四条分支。"""

    def test_same_day_uses_engine_target(self) -> None:
        '''两腿同日：采用引擎的择时目标仓位（等价改造前的重建）。'''
        positions = compose_two_leg_positions(
            previous={"old": 0.6},
            selection_target={"a": 0.4, "b": 0.4},
            timing_target_exposure=0.8,
            should_select=True,
            should_scale_risk=True,
        )
        assert positions == {"a": 0.4, "b": 0.4}

    def test_same_day_does_not_keep_stale_components(self) -> None:
        """两腿同日必须换成分（不能沿用调仓前的旧成分，否则择时只调仓位不换股）。"""
        positions = compose_two_leg_positions(
            previous={"old": 0.8},
            selection_target={"new": 0.5},
            timing_target_exposure=0.5,
            should_select=True,
            should_scale_risk=True,
        )
        assert positions == {"new": 0.5}

    def test_selection_only_keeps_current_exposure(self) -> None:
        """仅选股腿：换成新成分并把总量维持在调仓前水平。"""
        positions = compose_two_leg_positions(
            previous={"old": 0.6},
            selection_target={"a": 0.4, "b": 0.4},
            timing_target_exposure=0.8,
            should_select=True,
            should_scale_risk=False,
        )
        assert positions == {"a": 0.3, "b": 0.3}

    def test_selection_only_first_build_uses_engine_exposure(self) -> None:
        """首次建仓（无持仓）：直接使用引擎的择时目标仓位。"""
        positions = compose_two_leg_positions(
            previous={},
            selection_target={"a": 0.4, "b": 0.4},
            timing_target_exposure=0.8,
            should_select=True,
            should_scale_risk=False,
        )
        assert positions == {"a": 0.4, "b": 0.4}

    def test_risk_only_scales_without_changing_components(self) -> None:
        """仅风险腿：成分与相对权重不变，只缩放到择时目标仓位。"""
        positions = compose_two_leg_positions(
            previous={"a": 0.3, "b": 0.3},
            selection_target={"ignored": 1.0},
            timing_target_exposure=0.2,
            should_select=False,
            should_scale_risk=True,
        )
        assert positions == {"a": 0.1, "b": 0.1}

    def test_risk_only_does_not_open_position(self) -> None:
        """仅风险腿且当前空仓：不建仓（风险腿不选股）。"""
        positions = compose_two_leg_positions(
            previous={},
            selection_target={"a": 0.8},
            timing_target_exposure=0.8,
            should_select=False,
            should_scale_risk=True,
        )
        assert positions == {}

    def test_empty_selection_target_liquidates(self) -> None:
        '''引擎无入选资产：整仓清空（不被"维持当前仓位"规则救回）。'''
        positions = compose_two_leg_positions(
            previous={"old": 0.8},
            selection_target={},
            timing_target_exposure=0.8,
            should_select=True,
            should_scale_risk=False,
        )
        assert positions == {}

    def test_no_leg_holds_current_positions(self) -> None:
        """两腿皆不到期：维持现有持仓。"""
        positions = compose_two_leg_positions(
            previous={"a": 0.3},
            selection_target={"b": 0.9},
            timing_target_exposure=0.9,
            should_select=False,
            should_scale_risk=False,
        )
        assert positions == {"a": 0.3}


def _stub_loop_service(
    engine_positions: list[dict[str, float]],
    timing_exposures: list[float],
) -> tuple[BacktestService, dict[str, Any]]:
    """构造逐日返回指定持仓与择时目标仓位的内存回测服务。

    Args:
        engine_positions: 每个交易日引擎返回的目标成分（按顺序取用，越界复用最后一项）。
        timing_exposures: 每个交易日引擎给出的目标总仓位。

    Returns:
        (服务实例, 上下文) 二元组；上下文含回测行与落库收集列表。
    """
    svc = BacktestService(db=MagicMock())
    svc._ensure_market_scope_bars = MagicMock()
    svc._get_lookback_days = MagicMock(return_value=90)
    svc._write_index_results = MagicMock(return_value=(0, 0))
    bars = {
        ("000300", d): SimpleNamespace(
            close_price=100.0 + i,
            open_price=100.0 + i,
            high_price=100.0 + i,
            low_price=100.0 + i,
        )
        for i, d in enumerate(DATES)
    }
    universe = [{"index_code": "000300", "name_cn": "000300", "category": "broad_index"}]
    svc._prepare_backtest_data = MagicMock(
        return_value=(universe, ["000300"], list(DATES), bars, {}, {})
    )
    svc._factor_provider = MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = {
        d: {("000300", "return_5d"): 1.0} for d in DATES
    }
    svc._context_builder = MagicMock()
    svc._context_builder.build.side_effect = lambda config, trade_date, **kw: EngineContext(
        trade_date=trade_date,
        universe=list(universe),
        asset_factors={},
    )
    call_index = {"i": 0}

    def _run_engine(
        config: StrategyConfig, context: EngineContext, include_details: bool = False
    ) -> EngineResult:
        """按预设序列逐日返回持仓与择时目标仓位。"""
        idx = min(call_index["i"], len(engine_positions) - 1)
        call_index["i"] += 1
        positions = engine_positions[idx]
        exposure = timing_exposures[idx]
        return EngineResult(
            trade_date=context.trade_date,
            strategy_id=config.strategy_id,
            timing=TimingSignal(
                regime="offensive", confidence=80.0, label="进攻", factors={}
            ),
            scores={"000300": 1.0},
            rankings=[],
            positions=positions,
            total_exposure=exposure,
            cash_ratio=round(1.0 - exposure, 4),
            strategy_results=[],
        )

    svc._engine = MagicMock()
    svc._engine.run.side_effect = _run_engine
    # daily 频率不需要真实日历；显式注入替身以覆盖需要日历时的情况
    svc._rebalance_scheduler = DefaultRebalanceScheduler(_WeekdayCalendar())
    collected: list[Any] = []
    svc._backtest_repo = MagicMock()
    svc._backtest_repo.add_daily_result.side_effect = collected.append
    row = BacktestRunModel(
        backtest_id="bt-two-leg",
        strategy_id="t_two_leg",
        start_date=DATES[0],
        end_date=DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300"]},
        params={"_execution_model": "t_plus_1_open", "_data_quality_mode": "warn"},
        status="running",
    )
    return svc, {"row": row, "collected": collected}


def _config(rebalance: RebalanceConfig | None) -> StrategyConfig:
    """构造带双腿调仓配置的最小策略配置。"""
    return StrategyConfig(
        strategy_id="t_two_leg",
        display_name="t_two_leg",
        index_codes=["000300"],
        score=ScoreConfig(factors={"return_5d": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight"),
        risk=RiskConfig(max_asset_weight=1.0),
        rebalance=rebalance,
    )


class TestLoopLegs:
    """回测主循环的双腿调度。"""

    def test_identical_legs_match_legacy_daily_rebuild(self) -> None:
        '''两腿同频（每日）时，每日按"新成分 + 择时仓位"重建，等价改造前行为。'''
        # 引擎输出 = 择时 regime 对应的目标仓位（equal_weight 下每腿 = exposure / n）
        exposures = [0.8, 0.5, 0.2]
        engine_positions = [{"000300": exposure} for exposure in exposures]
        svc, ctx = _stub_loop_service(engine_positions, exposures)
        svc._run_backtest_loop("bt-two-leg", ctx["row"], _config(RebalanceConfig()))

        rows = ctx["collected"]
        assert [row.total_exposure for row in rows] == exposures
        assert [row.positions for row in rows] == [{"000300": e} for e in exposures]

    def test_split_legs_components_only_change_on_selection_days(self) -> None:
        """选股腿只在周四换成分；周五/周一仅风险腿到期，成分必须沿用、只缩放仓位。"""
        config = _config(
            RebalanceConfig(
                selection={"frequency": "weekly", "day_of_week": 3},  # 周四
                risk={"frequency": "daily"},
            )
        )
        engine_positions = [
            {"a": 0.6, "b": 0.2},  # 第 1 日（周四）选股腿建仓
            {"c": 0.4, "d": 0.4},  # 第 2 日（周五）引擎给了别的成分，但选股腿不到期
            {"c": 0.4, "d": 0.4},  # 第 3 日（周一）同上
        ]
        exposures = [0.8, 0.4, 0.2]
        svc, ctx = _stub_loop_service(engine_positions, exposures)
        svc._run_backtest_loop("bt-two-leg", ctx["row"], config)

        rows = ctx["collected"]
        assert [row.total_exposure for row in rows] == [0.8, 0.4, 0.2]
        # 第 2、3 日不得换成引擎给出的 c/d 成分，必须在 a/b 上等比缩放
        assert rows[1].positions == {"a": 0.3, "b": 0.1}
        assert rows[2].positions == {"a": 0.15, "b": 0.05}
        # 换手 = Σ|Δw|/2 = 上一日总仓位的一半（按 0.8→0.4→0.2 等比缩减）
        assert [row.turnover for row in rows] == [0.4, 0.2, 0.1]

    def test_identical_legs_follow_engine_components_every_day(self) -> None:
        """两腿同频（每日）时成分每日随引擎更新（等价改造前行为）。"""
        config = _config(RebalanceConfig())  # 默认两腿每日
        # 引擎输出本身已按择时目标仓位归一（总暴露 = 目标仓位）；第 3 日沿用第 2 日
        engine_positions = [{"a": 0.8}, {"b": 0.5}, {"b": 0.5}]
        exposures = [0.8, 0.5, 0.5]
        svc, ctx = _stub_loop_service(engine_positions, exposures)
        svc._run_backtest_loop("bt-two-leg", ctx["row"], config)

        rows = ctx["collected"]
        assert rows[0].positions == {"a": 0.8}
        assert rows[1].positions == {"b": 0.5}
        assert [row.total_exposure for row in rows] == exposures
