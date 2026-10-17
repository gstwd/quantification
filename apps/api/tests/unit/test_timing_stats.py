"""择时模块可观测性单元测试。

覆盖：
- ``domain.research.timing`` 纯函数：regime 翻转 / 占比、综合分分位、分 regime 收益、
  换手按调仓腿拆分（含 T+1 收盘的 1 行成交滞后）与汇总；
- 执行器输出的总仓位变动换手（``exposure_turnover``）；
- 回测主循环把择时统计写入汇总指标 ``timing_stats``，详情按 schema 解析，
  未配置择时 / 存量回测不产生该块。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from quant_etf_api.domain.portfolio.execution import BacktestExecutor
from quant_etf_api.domain.research.timing import (
    TimingDay,
    compute_timing_stats,
    count_regime_flips,
    regime_return_stats,
    regime_shares,
    score_distribution,
    turnover_breakdown,
)
from quant_etf_api.domain.strategies.models import TimingSignal
from quant_etf_api.domain.strategies.rebalance import RebalanceLegs
from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
    TimingConfig,
)
from quant_etf_api.factors.compute import FactorMatrix
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService, _parse_timing_stats


def _day(
    regime: str | None,
    ret: float = 0.0,
    *,
    score: float | None = None,
    bench: float | None = None,
    exposure: float | None = None,
    target: float | None = None,
    turnover: float = 0.0,
    exposure_turnover: float = 0.0,
    risk: bool = False,
    selection: bool = False,
    degraded: bool = False,
) -> TimingDay:
    """构造一条择时观测记录。"""
    return TimingDay(
        regime=regime,
        score=score,
        degraded=degraded,
        portfolio_return=ret,
        benchmark_return=bench,
        exposure=exposure,
        target_exposure=target,
        turnover=turnover,
        exposure_turnover=exposure_turnover,
        risk_order=risk,
        selection_order=selection,
    )


class TestRegimeFlipsAndShares:
    """regime 翻转与时间占比。"""

    @pytest.mark.parametrize(
        "regimes,expected",
        [
            ([], 0),
            (["neutral"], 0),
            (["neutral", "neutral", "offensive"], 1),
            (["offensive", "defensive", "offensive", "neutral"], 3),
            # 降级日（None）不算切换、也不打断比较：前后相同则不翻转
            (["defensive", None, "defensive"], 0),
            (["defensive", None, "offensive"], 1),
        ],
    )
    def test_count_regime_flips(self, regimes: list[str | None], expected: int) -> None:
        """相邻有效 regime 不同才计一次翻转。"""
        assert count_regime_flips(regimes) == expected

    def test_regime_shares_lists_all_regimes(self) -> None:
        """占比按有效 regime 计，未出现的 regime 记 0。"""
        shares = regime_shares(["offensive", "offensive", "defensive", None])
        assert shares == {"offensive": pytest.approx(0.6667), "neutral": 0.0, "defensive": 0.3333}

    def test_regime_shares_empty_without_timing(self) -> None:
        """没有任何 regime（未配置择时）时返回空字典。"""
        assert regime_shares([None, None]) == {}


class TestScoreDistribution:
    """择时综合分分位。"""

    def test_quantiles_match_linear_interpolation(self) -> None:
        """分位按线性插值（numpy 默认口径），降级日 None 不计。"""
        dist = score_distribution([10.0, 20.0, 30.0, 40.0, 50.0, None], quantiles=(0.25, 0.5, 0.9))
        assert dist.count == 5
        assert dist.mean == 30.0
        assert dist.min == 10.0
        assert dist.max == 50.0
        assert [(q.quantile, q.value) for q in dist.quantiles] == [
            (0.25, 20.0),
            (0.5, 30.0),
            (0.9, 46.0),
        ]
        assert dist.std == pytest.approx(15.81, abs=0.01)

    def test_empty_scores(self) -> None:
        """全部缺失时只有 count=0。"""
        dist = score_distribution([None, float("nan")])
        assert dist.count == 0
        assert dist.quantiles == []


class TestRegimeReturnStats:
    """分 regime 收益统计。"""

    def test_groups_by_decision_day_regime(self) -> None:
        """按决策日 regime 归组，给出组合与基准的均值、几何年化与平均仓位。"""
        days = [
            _day("offensive", 1.0, bench=2.0, exposure=0.9),
            _day("offensive", -0.5, bench=-1.0, exposure=0.9),
            _day("defensive", 0.1, bench=-2.0, exposure=0.1),
            _day(None, 5.0),
        ]
        stats = {item.regime: item for item in regime_return_stats(days, trading_days_per_year=2)}
        offensive = stats["offensive"]
        assert offensive.days == 2
        assert offensive.share == pytest.approx(0.6667)
        assert offensive.mean_daily_return_pct == pytest.approx(0.25)
        assert offensive.average_exposure == pytest.approx(0.9)
        # 年化系数 2、恰好 2 天：年化 = 累计收益 (1.01 × 0.995 − 1)
        assert offensive.annualized_return_pct == pytest.approx(0.495, abs=0.006)
        assert offensive.benchmark_mean_daily_return_pct == pytest.approx(0.5)
        assert stats["defensive"].benchmark_mean_daily_return_pct == pytest.approx(-2.0)
        # 未出现的 regime 也列出
        assert stats["neutral"].days == 0
        assert stats["neutral"].mean_daily_return_pct is None


class TestTurnoverBreakdown:
    """换手按调仓腿拆分。"""

    def test_open_model_turnover_on_order_row(self) -> None:
        """T+1 开盘：换手记在下单当行，风险腿 / 仅风险腿 / 仓位变动分别占比。"""
        days = [
            _day("offensive", turnover=0.4, exposure_turnover=0.4, risk=True, selection=True),
            _day("offensive", turnover=0.2, selection=True),
            _day("defensive", turnover=0.3, exposure_turnover=0.3, risk=True),
            _day("defensive", turnover=0.1, selection=True),
        ]
        result = turnover_breakdown(days, fill_lag=0, trading_days_per_year=4)
        assert result.total_turnover == pytest.approx(1.0)
        assert result.annualized_turnover == pytest.approx(1.0)
        assert result.risk_leg_turnover == pytest.approx(0.7)
        assert result.risk_leg_turnover_share == pytest.approx(0.7)
        assert result.risk_only_turnover_share == pytest.approx(0.3)
        assert result.exposure_turnover_share == pytest.approx(0.7)

    def test_close_model_aligns_fill_to_previous_order(self) -> None:
        """T+1 收盘：第 i 行的指令在第 i+1 行成交，换手按上一行的调仓腿归属。"""
        days = [
            _day("offensive", turnover=0.0, risk=True),
            _day("offensive", turnover=0.5, selection=True),
            _day("defensive", turnover=0.25),
        ]
        result = turnover_breakdown(days, fill_lag=1)
        assert result.risk_leg_turnover == pytest.approx(0.5)
        assert result.risk_leg_turnover_share == pytest.approx(0.6667)
        assert result.risk_only_turnover_share == pytest.approx(0.6667)

    def test_zero_turnover_shares_are_none(self) -> None:
        """无换手时占比无意义，返回 None。"""
        result = turnover_breakdown([_day("neutral", risk=True)])
        assert result.risk_leg_turnover_share is None
        assert result.exposure_turnover_share is None


class TestComputeTimingStats:
    """择时专项统计汇总。"""

    def test_none_without_timing(self) -> None:
        """未配置择时（regime 全空）时不产生统计。"""
        assert compute_timing_stats([_day(None), _day(None)]) is None
        assert compute_timing_stats([]) is None

    def test_execution_level_switches_only_count_risk_leg_days(self) -> None:
        """执行口径只比较风险腿到期日：regime 切换但目标仓位相同不计仓位切换。"""
        days = [
            _day("offensive", score=70.0, target=0.9, risk=True),
            _day("neutral", score=50.0, target=0.9),
            _day("neutral", score=55.0, target=0.9, risk=True),
            _day("defensive", score=30.0, target=0.1),
            _day("offensive", score=66.0, target=0.9, risk=True),
            _day("neutral", score=None, target=0.9, degraded=True),
        ]
        stats = compute_timing_stats(
            days, offensive_threshold=65.0, defensive_threshold=40.0, trading_days_per_year=6
        )
        assert stats is not None
        assert stats.trading_days == 6
        assert stats.degraded_days == 1
        # 逐日信号口径：off→neu→neu→def→off→neu 共 4 次
        assert stats.regime_flips == 4
        assert stats.regime_flips_per_year == pytest.approx(4.0)
        # 风险腿到期日 regime：off → neu → off，切换 2 次；目标仓位 0.9 → 0.9 → 0.9 不变
        assert stats.risk_leg_days == 3
        assert stats.risk_leg_regime_changes == 2
        assert stats.target_exposure_switches == 0
        assert stats.score.count == 5
        assert stats.offensive_threshold == 65.0
        payload = stats.to_dict()
        assert payload["turnover"]["total_turnover"] == 0.0
        assert [item["regime"] for item in payload["regimes"]] == [
            "offensive",
            "neutral",
            "defensive",
        ]


class TestExposureTurnover:
    """执行器输出的总仓位变动换手。"""

    def _bars(self, days: list[date]) -> dict[tuple[str, date], Any]:
        """平盘行情（开盘 = 收盘 = 100）。"""
        return {
            (code, d): SimpleNamespace(close_price=100.0, open_price=100.0)
            for code in ("a", "b")
            for d in days
        }

    def test_open_model_exposure_turnover(self) -> None:
        """T+1 开盘：0.8 → 0.2 的缩仓产生 0.3 的仓位变动换手；成分轮换不计入。"""
        days = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)]
        bars = self._bars(days)
        executor = BacktestExecutor("t_plus_1_open")
        first = executor.step(
            trade_date=days[0],
            next_date=days[1],
            target_positions={"a": 0.8},
            should_rebalance=True,
            return_bars=bars,
            quality_bars=bars,
        )
        rotate = executor.step(
            trade_date=days[1],
            next_date=days[2],
            target_positions={"b": 0.8},
            should_rebalance=True,
            return_bars=bars,
            quality_bars=bars,
        )
        shrink = executor.step(
            trade_date=days[2],
            next_date=days[3],
            target_positions={"b": 0.2},
            should_rebalance=True,
            return_bars=bars,
            quality_bars=bars,
        )
        assert first.exposure_turnover == pytest.approx(0.4)
        assert rotate.turnover == pytest.approx(0.8)
        assert rotate.exposure_turnover == pytest.approx(0.0)
        assert shrink.exposure_turnover == pytest.approx(0.3)

    def test_close_model_exposure_turnover_on_fill_day(self) -> None:
        """T+1 收盘：指令次日成交，仓位变动换手记在成交行。"""
        days = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
        bars = self._bars(days)
        executor = BacktestExecutor("t_plus_1_close")
        order = executor.step(
            trade_date=days[0],
            next_date=days[1],
            target_positions={"a": 0.6},
            should_rebalance=True,
            return_bars=bars,
            quality_bars=bars,
        )
        fill = executor.step(
            trade_date=days[1],
            next_date=days[2],
            target_positions={},
            should_rebalance=False,
            return_bars=bars,
            quality_bars=bars,
        )
        assert order.exposure_turnover == 0.0
        assert fill.exposure_turnover == pytest.approx(0.3)


# ── 回测主循环集成 ─────────────────────────────────────────────────────────

_WINDOW = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)]


class _SnapshotCalendar:
    """测试区间的交易日快照，避免依赖外部日历。"""

    def is_trading_day(self, day: date) -> bool:
        """按快照判断交易日。"""
        return day in _WINDOW


@pytest.fixture(autouse=True)
def _use_calendar_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """替换交易日历解析为明确快照。"""

    def resolve_snapshot(db: object, **kwargs: object) -> tuple[_SnapshotCalendar, str]:
        """提供测试区间的交易日快照。"""
        return _SnapshotCalendar(), "database"

    monkeypatch.setattr(
        "quant_etf_api.services.backtest_service.resolve_trading_calendar", resolve_snapshot
    )


def _loop_service(
    regimes: list[str | None], scores: list[float | None], exposures: list[float]
) -> tuple[BacktestService, BacktestRunModel, list[Any]]:
    """构造逐日返回预设 regime / 综合分 / 目标仓位的内存回测服务。"""
    svc = BacktestService(db=MagicMock())
    svc._ensure_market_scope_bars = MagicMock()
    svc._get_lookback_days = MagicMock(return_value=90)
    svc._write_index_results = MagicMock(return_value=(0, 0))
    bars = {
        (code, d): SimpleNamespace(
            close_price=100.0 + i, open_price=100.0 + i, high_price=100.0 + i, low_price=100.0 + i
        )
        for code in ("000300",)
        for i, d in enumerate(_WINDOW)
    }
    universe = [{"index_code": "000300", "name_cn": "000300"}]
    svc._prepare_backtest_data = MagicMock(
        return_value=(universe, ["000300"], list(_WINDOW), bars, {}, {})
    )
    svc._factor_provider = MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = FactorMatrix(values={})
    svc._context_builder = MagicMock()
    svc._context_builder.build.side_effect = lambda config, trade_date, **kw: EngineContext(
        trade_date=trade_date, universe=list(universe), asset_factors={}
    )
    calls = {"i": 0}

    def _run_engine(
        config: StrategyConfig, context: EngineContext, include_details: bool = False
    ) -> EngineResult:
        """按预设序列返回择时结果与持仓。"""
        i = calls["i"]
        calls["i"] += 1
        regime = regimes[i]
        timing = None
        if regime is not None:
            factors: dict[str, Any] = (
                {"degraded": True} if scores[i] is None else {"composite_score": scores[i]}
            )
            timing = TimingSignal(regime=regime, confidence=60.0, label=regime, factors=factors)
        positions = {"000300": exposures[i]}
        return EngineResult(
            trade_date=context.trade_date,
            strategy_id=config.strategy_id,
            timing=timing,
            scores={"000300": 1.0},
            rankings=[],
            positions=positions,
            total_exposure=exposures[i],
            cash_ratio=round(1.0 - exposures[i], 4),
            strategy_results=[],
            timing_target_exposure=exposures[i],
        )

    svc._engine = MagicMock()
    svc._engine.run.side_effect = _run_engine
    svc._select_legs = MagicMock(return_value=RebalanceLegs(selection=True, risk=True))
    collected: list[Any] = []
    svc._backtest_repo = MagicMock()
    svc._backtest_repo.add_daily_result.side_effect = collected.append
    row = BacktestRunModel(
        backtest_id="bt-timing",
        strategy_id="t_timing",
        start_date=_WINDOW[0],
        end_date=_WINDOW[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300"]},
        params={"_execution_model": "t_plus_1_open", "_data_quality_mode": "warn"},
        status="running",
    )
    return svc, row, collected


def _config(with_timing: bool) -> StrategyConfig:
    """最小策略配置（可选带择时块）。"""
    return StrategyConfig(
        strategy_id="t_timing",
        display_name="t_timing",
        index_codes=["000300"],
        score=ScoreConfig(factors={"return_5d": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight"),
        risk=RiskConfig(max_asset_weight=1.0),
        timing=(
            TimingConfig(factors={"return_60d": 1.0}, thresholds={"offensive": 65, "defensive": 40})
            if with_timing
            else None
        ),
    )


class TestLoopTimingStats:
    """回测主循环写入择时统计。"""

    def test_timing_stats_written_to_metrics(self) -> None:
        """配置择时时汇总指标带 timing_stats，降级日综合分不计入分布。"""
        svc, row, _ = _loop_service(
            ["offensive", "neutral", "defensive"], [70.0, None, 30.0], [0.9, 0.9, 0.1]
        )
        outcome = svc._run_backtest_loop("bt-timing", row, _config(True))

        raw = outcome["metrics"]["timing_stats"]
        assert len(outcome["timing_days"]) == 3
        stats = _parse_timing_stats(outcome["metrics"])
        assert stats is not None
        assert stats.trading_days == 3
        assert stats.degraded_days == 1
        assert stats.regime_flips == 2
        assert stats.score.count == 2
        assert stats.defensive_threshold == 40.0
        assert [item.days for item in stats.regimes] == [1, 1, 1]
        # 0 → 0.9 → 0.9 → 0.1：仓位变动换手 ≈ 0.45 + 0.4（持仓随行情小幅漂移）
        assert stats.turnover.exposure_turnover == pytest.approx(0.85, abs=0.01)
        assert stats.turnover.risk_leg_turnover_share == pytest.approx(1.0)
        assert raw["target_exposure_switches"] == 1

    def test_no_timing_stats_without_timing(self) -> None:
        """未配置择时时不写 timing_stats 键，详情解析为 None。"""
        svc, row, _ = _loop_service([None, None, None], [None, None, None], [0.5, 0.5, 0.5])
        outcome = svc._run_backtest_loop("bt-timing", row, _config(False))

        assert "timing_stats" not in outcome["metrics"]
        assert _parse_timing_stats(outcome["metrics"]) is None

    @pytest.mark.parametrize("metrics", [None, {}, {"timing_stats": "bad"}, {"timing_stats": {}}])
    def test_legacy_or_invalid_metrics_parse_to_none(self, metrics: Any) -> None:
        """存量回测（无该键）或结构非法时按缺失处理，不报错。"""
        assert _parse_timing_stats(metrics) is None
