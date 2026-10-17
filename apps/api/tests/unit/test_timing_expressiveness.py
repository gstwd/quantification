"""择时模块表达能力扩展的回归测试。

覆盖：
- 状态层纯函数：滞回、无信号维持、确认天数、最短持有；默认参数为恒等变换；
- 规则模式：防守优先、AND / OR、跨因子比较、缺失语义、全部缺失降级；
- 按因子指定代理：代理分组选值、TimingConfig 辅助方法与引用收集；
- 择时评估：降级回退不受确认约束、hold_previous 沿用状态中的有效 regime；
- 回测主循环逐日传递状态；实时路径回放历史重建状态；
- 配置校验：综合方式、规则组、factor_proxies 的错误与警告。
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from quant_etf_api.domain.strategies.timing_proxy import (
    TimingProxySelection,
    select_grouped_timing_proxy_values,
    select_timing_proxy_values,
)
from quant_etf_api.domain.strategies.timing_state import (
    TimingState,
    resolve_candidate_regime,
    step_timing_state,
)
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import (
    FilterConfig,
    FilterRule,
    PortfolioConfig,
    RankConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
    TimingConfig,
    TimingRulesConfig,
    TimingStateConfig,
    TimingThresholds,
    iter_factor_refs,
)
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.engine.score import DefaultScoreCalculator
from quant_etf_api.engine.timing import evaluate_timing
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.factors.compute import FactorMatrix
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import StrategyConfigService

TRADE_DATE = date(2025, 1, 15)


def _context(
    market_factors: dict[str, float | None],
    timing_state: TimingState | None = None,
    timing_proxy: TimingProxySelection | None = None,
) -> EngineContext:
    """构造只含择时因子的引擎上下文。"""
    return EngineContext(
        trade_date=TRADE_DATE,
        universe=[],
        market_factors=market_factors,
        timing_state=timing_state,
        timing_proxy=timing_proxy,
    )


def _rules_timing(
    offensive: FilterConfig | None = None,
    defensive: FilterConfig | None = None,
    **overrides: Any,
) -> TimingConfig:
    """构造规则模式择时配置。"""
    return TimingConfig(
        combine="rules",
        rules=TimingRulesConfig(offensive=offensive, defensive=defensive),
        **overrides,
    )


def _rule(factor: str, op: str, value: float | None = None, **kw: Any) -> FilterRule:
    """构造单条规则。"""
    return FilterRule(factor=factor, op=op, value=value, **kw)


def _run_states(
    candidates: list[str], *, confirm_days: int = 1, min_hold_days: int = 0
) -> list[str]:
    """按候选 regime 序列逐日推进状态，返回有效 regime 序列。"""
    state: TimingState | None = None
    regimes: list[str] = []
    for candidate in candidates:
        state = step_timing_state(
            state, candidate, confirm_days=confirm_days, min_hold_days=min_hold_days
        )
        regimes.append(state.regime)
    return regimes


# ── 1. 状态层纯函数 ──────────────────────────────────────────────────────


class TestTimingStateMachine:
    """确认天数与最短持有期。"""

    _SEQ = ["offensive", "neutral", "offensive", "defensive", "defensive", "defensive", "neutral"]

    def test_default_is_identity(self) -> None:
        """confirm_days=1、min_hold_days=0 时有效 regime 恒等于候选 regime。"""
        assert _run_states(self._SEQ) == self._SEQ

    def test_confirm_days_requires_consecutive_candidates(self) -> None:
        """候选需连续出现 confirm_days 日才切换；中途打断重新计数。"""
        assert _run_states(self._SEQ, confirm_days=2) == [
            "offensive",
            "offensive",  # neutral 只出现 1 日
            "offensive",
            "offensive",  # defensive 第 1 日
            "defensive",  # 第 2 日确认切换
            "defensive",
            "defensive",
        ]

    def test_min_hold_days_blocks_early_switch(self) -> None:
        """切换后至少持有 min_hold_days 个决策日才允许再切换。"""
        seq = ["offensive", "defensive", "defensive", "offensive", "offensive"]
        assert _run_states(seq, min_hold_days=3) == [
            "offensive",
            "offensive",  # 已持有 1 日 < 3
            "offensive",  # 已持有 2 日 < 3
            "offensive",  # 候选回到 offensive，无需切换
            "offensive",
        ]

    def test_pending_reported_in_state(self) -> None:
        """未确认的候选记录在状态中，供明细展示。"""
        state = step_timing_state(
            TimingState(regime="offensive", held_days=5), "defensive", confirm_days=3,
            min_hold_days=0,
        )
        assert state.regime == "offensive"
        assert state.pending == "defensive"
        assert state.pending_days == 1
        assert state.to_detail() == {"held_days": 6, "pending_regime": "defensive", "pending_days": 1}


class TestCandidateRegime:
    """滞回与无信号维持。"""

    @pytest.mark.parametrize(
        ("raw", "composite", "previous", "expected"),
        [
            ("neutral", 60.0, "offensive", "offensive"),  # 60 ≥ 65 − 10，保持进攻
            ("neutral", 54.0, "offensive", "neutral"),  # 跌出滞回带
            ("neutral", 40.0, "defensive", "defensive"),  # 40 ≤ 35 + 10，保持防守
            ("defensive", 20.0, "offensive", "defensive"),  # 跨到对侧极端不受滞回阻止
            ("neutral", 60.0, None, "neutral"),  # 无历史时不施加
        ],
    )
    def test_hysteresis(
        self, raw: str, composite: float, previous: str | None, expected: str
    ) -> None:
        """滞回只延长留在原 regime 的区间。"""
        assert (
            resolve_candidate_regime(
                raw,
                composite,
                previous,
                hysteresis=10.0,
                hold_on_neutral=False,
                offensive_threshold=65.0,
                defensive_threshold=35.0,
            )
            == expected
        )

    def test_hold_on_neutral(self) -> None:
        """候选为中性时沿用上一有效 regime；无综合分（规则模式）同样生效。"""
        assert (
            resolve_candidate_regime(
                "neutral",
                None,
                "defensive",
                hysteresis=0.0,
                hold_on_neutral=True,
                offensive_threshold=65.0,
                defensive_threshold=35.0,
            )
            == "defensive"
        )

    def test_hysteresis_ignored_without_composite(self) -> None:
        """没有综合分（规则模式 / 降级）时滞回不生效。"""
        assert (
            resolve_candidate_regime(
                "neutral",
                None,
                "offensive",
                hysteresis=10.0,
                hold_on_neutral=False,
                offensive_threshold=65.0,
                defensive_threshold=35.0,
            )
            == "neutral"
        )


# ── 2. 规则模式 ──────────────────────────────────────────────────────────


class TestRulesTiming:
    """规则模式的判定顺序与缺失语义。"""

    _OFFENSIVE = FilterConfig(logic="AND", rules=[_rule("trend", "gt", 0), _rule("mom", "gt", 0)])
    _DEFENSIVE = FilterConfig(logic="OR", rules=[_rule("trend", "lt", -1), _rule("mom", "lt", -5)])

    @pytest.mark.parametrize(
        ("trend", "mom", "expected"),
        [
            (1.0, 1.0, "offensive"),  # 进攻组 AND 全部满足
            (1.0, -1.0, "neutral"),  # 进攻不满足、防守不满足
            (1.0, -6.0, "defensive"),  # 防守组 OR 任一满足
            (-2.0, 1.0, "defensive"),
        ],
    )
    def test_defensive_first_then_offensive(self, trend: float, mom: float, expected: str) -> None:
        """防守组优先，其次进攻组，都不满足为中性。"""
        timing = _rules_timing(self._OFFENSIVE, self._DEFENSIVE)
        score, regime, details = DefaultScoreCalculator().calculate_timing(
            timing, _context({"trend": trend, "mom": mom})
        )
        assert regime == expected
        assert details["combine"] == "rules"
        assert details["composite_score"] == score
        assert details["rule_groups"]["defensive"]["matched"] is (expected == "defensive")

    def test_compare_to_uses_market_factor(self) -> None:
        """跨因子比较读取另一个市场级因子的原值。"""
        timing = _rules_timing(
            offensive=FilterConfig(rules=[FilterRule(factor="fast", op="gt", compare_to="slow")])
        )
        _s, regime, _d = DefaultScoreCalculator().calculate_timing(
            timing, _context({"fast": 11.0, "slow": 10.0})
        )
        assert regime == "offensive"

    @pytest.mark.parametrize(("strategy", "expected"), [("fail", "neutral"), ("pass", "offensive")])
    def test_missing_strategy(self, strategy: str, expected: str) -> None:
        """单个因子缺失按规则的 missing_strategy 处理。"""
        timing = _rules_timing(
            offensive=FilterConfig(
                rules=[_rule("trend", "gt", 0), _rule("mom", "gt", 0, missing_strategy=strategy)]
            )
        )
        _s, regime, details = DefaultScoreCalculator().calculate_timing(
            timing, _context({"trend": 1.0, "mom": None})
        )
        assert regime == expected
        assert details["missing_factors"] == ["mom"]

    def test_all_missing_degrades(self) -> None:
        """规则引用的因子全部缺失时按 on_degraded 回退并标记降级。"""
        timing = _rules_timing(self._OFFENSIVE, self._DEFENSIVE, on_degraded="defensive")
        _s, regime, details = DefaultScoreCalculator().calculate_timing(
            timing, _context({"trend": None, "mom": None})
        )
        assert regime == "defensive"
        assert details["degraded"] is True

    def test_engine_run_with_rules_and_exposure(self) -> None:
        """引擎按规则模式 regime 决定目标仓位。"""
        config = StrategyConfig(
            strategy_id="r",
            display_name="r",
            score=ScoreConfig(factors={"val": 1.0}),
            timing=_rules_timing(self._OFFENSIVE, self._DEFENSIVE),
            portfolio=PortfolioConfig(
                method="equal_weight",
                timing_exposure={"offensive": 0.9, "neutral": 0.5, "defensive": 0.1},
            ),
        )
        context = EngineContext(
            trade_date=TRADE_DATE,
            universe=[{"index_code": "A", "name_cn": "A"}],
            asset_factors={("A", "val"): 50.0},
            market_factors={"trend": -3.0, "mom": 1.0},
        )
        result = StrategyEngine().run(config, context, include_details=False)
        assert result.timing is not None and result.timing.regime == "defensive"
        assert result.timing_target_exposure == pytest.approx(0.1)


# ── 3. 按因子指定代理 ────────────────────────────────────────────────────


class TestFactorProxies:
    """代理分组与 TimingConfig 辅助方法。"""

    def test_config_helpers(self) -> None:
        """factor_refs / proxy_groups / all_proxy_codes 覆盖规则引用与单独代理。"""
        timing = _rules_timing(
            offensive=FilterConfig(rules=[_rule("pv", "gt", 1)]),
            defensive=FilterConfig(rules=[FilterRule(factor="m", op="lt", compare_to="n")]),
            factor_proxies={"pv": ["000852", "000905"]},
        )
        assert timing.factor_refs() == ["m", "n", "pv"]
        assert timing.proxy_groups() == [(["000300"], ["m", "n"]), (["000852", "000905"], ["pv"])]
        assert timing.all_proxy_codes() == ["000300", "000852", "000905"]

    def test_iter_factor_refs_collects_rule_refs(self) -> None:
        """引用收集（加载、校验共用）包含规则模式引用的因子。"""
        config = StrategyConfig(
            strategy_id="r",
            display_name="r",
            score=ScoreConfig(factors={"val": 1.0}),
            timing=_rules_timing(offensive=FilterConfig(rules=[_rule("pv", "gt", 1)])),
        )
        assert ("timing", "择时", "pv") in iter_factor_refs(config)

    def test_single_group_matches_legacy(self) -> None:
        """只有默认组时与单组代理选择完全一致。"""
        values = {("000300", "a"): 1.0, ("000905", "a"): 2.0}
        grouped = select_grouped_timing_proxy_values(
            [(["000300", "000905"], ["a"])], lambda c, r: values.get((c, r))
        )
        legacy = select_timing_proxy_values(
            ["000300", "000905"], ["a"], lambda c, r: values.get((c, r))
        )
        assert grouped == legacy

    def test_configured_groups(self) -> None:
        """各组全覆盖但落在不同代理上时为 configured 模式。"""
        values = {("000300", "m"): 1.0, ("000852", "pv"): 2.0}
        selection = select_grouped_timing_proxy_values(
            [(["000300"], ["m"]), (["000852"], ["pv"])], lambda c, r: values.get((c, r))
        )
        assert selection.values == {"m": 1.0, "pv": 2.0}
        assert selection.factor_proxies == {"m": "000300", "pv": "000852"}
        assert selection.mode == "configured"
        assert selection.proxy_index_code is None

    def test_uncovered_group_is_per_factor(self) -> None:
        """有组未全覆盖时退回 per_factor 模式。"""
        values = {("000300", "m"): 1.0}
        selection = select_grouped_timing_proxy_values(
            [(["000300"], ["m"]), (["000852"], ["pv"])], lambda c, r: values.get((c, r))
        )
        assert selection.values == {"m": 1.0, "pv": None}
        assert selection.mode == "per_factor"

    def test_backtest_context_uses_factor_proxy(self) -> None:
        """回测上下文按因子指定的代理取值。"""
        config = StrategyConfig(
            strategy_id="r",
            display_name="r",
            score=ScoreConfig(factors={"val": 1.0}),
            timing=TimingConfig(
                factors={"m": 1.0, "pv": 1.0}, factor_proxies={"pv": ["000852"]}
            ),
        )
        precomputed = {
            ("000300", "m"): 60.0,
            ("000300", "pv"): 10.0,
            ("000852", "pv"): 90.0,
        }
        context = ContextBuilder(mock.MagicMock(), factor_provider=mock.MagicMock()).build(
            config, TRADE_DATE, index_codes=[], all_bars={}, precomputed_factors=precomputed
        )
        assert context.market_factors == {"m": 60.0, "pv": 90.0}
        assert context.timing_proxy is not None and context.timing_proxy.mode == "configured"


# ── 4. 择时评估（原始判定 + 状态层） ─────────────────────────────────────


class TestEvaluateTiming:
    """evaluate_timing 的状态推进、降级与明细。"""

    def _timing(self, **state: Any) -> TimingConfig:
        return TimingConfig(
            factors={"t": 1.0},
            thresholds=TimingThresholds(offensive=65, defensive=35),
            state=TimingStateConfig(**state),
            on_degraded="hold_previous",
        )

    def test_identity_state_keeps_legacy_details(self) -> None:
        """恒等状态层不改变 regime，也不新增明细字段。"""
        signal, state = evaluate_timing(
            DefaultScoreCalculator(), TimingConfig(factors={"t": 1.0}), _context({"t": 90.0})
        )
        assert signal.regime == "offensive"
        assert "state" not in signal.factors and "raw_regime" not in signal.factors
        assert state == TimingState(regime="offensive")

    def test_confirm_holds_and_reports_raw(self) -> None:
        """确认期内维持原 regime，明细给出原始判定与待确认状态。"""
        signal, state = evaluate_timing(
            DefaultScoreCalculator(),
            self._timing(confirm_days=2),
            _context({"t": 10.0}, timing_state=TimingState(regime="offensive", held_days=3)),
        )
        assert signal.regime == "offensive"
        assert signal.factors["raw_regime"] == "defensive"
        assert signal.factors["state"]["pending_regime"] == "defensive"
        assert state.pending_days == 1

    def test_degraded_hold_previous_uses_state_regime(self) -> None:
        """降级日 hold_previous 沿用状态中的有效 regime。"""
        signal, state = evaluate_timing(
            DefaultScoreCalculator(),
            self._timing(confirm_days=3),
            _context({"t": None}, timing_state=TimingState(regime="defensive", held_days=2)),
        )
        assert signal.regime == "defensive"
        assert state.held_days == 3

    def test_degraded_fallback_bypasses_confirmation(self) -> None:
        """降级回退是显式策略：即使配置了确认天数也立即生效。"""
        timing = self._timing(confirm_days=5).model_copy(update={"on_degraded": "defensive"})
        signal, state = evaluate_timing(
            DefaultScoreCalculator(),
            timing,
            _context({"t": None}, timing_state=TimingState(regime="offensive", held_days=9)),
        )
        assert signal.regime == "defensive"
        assert state == TimingState(regime="defensive")


# ── 5. 回测主循环与实时回放 ──────────────────────────────────────────────

# 2025-01-06（周一）起连续 8 个工作日（跨周末），最后一日无收益窗口不形成决策
LOOP_DATES = [
    d
    for d in (date(2025, 1, 6) + timedelta(days=i) for i in range(12))
    if d.weekday() < 5
][:8]


def _loop_service(timing_values: list[float | None]) -> BacktestService:
    """构造使用真实上下文构建与真实引擎的内存回测服务（单择时因子 t）。"""
    svc = BacktestService(db=mock.MagicMock())
    svc._ensure_market_scope_bars = mock.MagicMock()
    svc._get_lookback_days = mock.MagicMock(return_value=90)
    svc._write_index_results = mock.MagicMock(return_value=(0, 0))
    bars = {
        (code, d): SimpleNamespace(
            close_price=100.0, open_price=100.0, high_price=100.0, low_price=100.0
        )
        for code in ("000300", "000905")
        for d in LOOP_DATES
    }
    universe = [
        {"index_code": "000300", "name_cn": "000300", "category": "broad_index"},
        {"index_code": "000905", "name_cn": "000905", "category": "broad_index"},
    ]
    svc._prepare_backtest_data = mock.MagicMock(
        return_value=(universe, ["000300", "000905"], list(LOOP_DATES), bars, {}, {})
    )
    values = {
        d: {("000300", "val"): 1.0, ("000905", "val"): 2.0, ("000300", "t"): t}
        for d, t in zip(LOOP_DATES, timing_values)
    }
    svc._factor_provider = mock.MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = FactorMatrix(values=values)
    svc._context_builder = ContextBuilder(mock.MagicMock(), factor_provider=svc._factor_provider)
    svc._engine = StrategyEngine()
    svc._backtest_repo = mock.MagicMock()
    return svc


def _loop_config(state: TimingStateConfig) -> StrategyConfig:
    """单择时因子（原值即 0-100 口径）的最小回测配置。"""
    return StrategyConfig(
        strategy_id="t_timing",
        display_name="t_timing",
        index_codes=["000300", "000905"],
        timing=TimingConfig(
            factors={"t": 1.0},
            thresholds=TimingThresholds(offensive=65, defensive=35),
            state=state,
        ),
        score=ScoreConfig(factors={"val": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight"),
        risk=RiskConfig(max_asset_weight=1.0),
    )


def _loop_row() -> BacktestRunModel:
    """构造运行中的回测行。"""
    return BacktestRunModel(
        backtest_id="bt-state",
        strategy_id="t_timing",
        start_date=LOOP_DATES[0],
        end_date=LOOP_DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300", "000905"]},
        params={"_execution_model": "t_plus_1_open", "_data_quality_mode": "warn"},
        status="running",
    )


class TestBacktestLoopState:
    """回测主循环逐日传递状态层状态。"""

    # 进攻 → 单日跌到防守 → 回到进攻 → 连续两日防守 → 进攻 …
    _VALUES: list[float | None] = [90.0, 10.0, 90.0, 10.0, 10.0, 90.0, 90.0, 90.0]

    @staticmethod
    def _regimes(svc: BacktestService) -> list[str | None]:
        """按交易日顺序取落库的逐日 regime。"""
        return [
            call.args[0].timing_regime
            for call in svc._backtest_repo.add_daily_result.call_args_list
        ]

    def test_identity_state_unchanged(self) -> None:
        """默认状态层下逐日 regime 与原始判定一致。"""
        svc = _loop_service(self._VALUES)
        svc._run_backtest_loop("bt-state", _loop_row(), _loop_config(TimingStateConfig()))
        assert self._regimes(svc) == [
            "offensive", "defensive", "offensive", "defensive", "defensive", "offensive", "offensive",
        ]

    def test_confirm_days_filters_single_day_flip(self) -> None:
        """confirm_days=2 过滤单日翻转，连续两日才切换。"""
        svc = _loop_service(self._VALUES)
        svc._run_backtest_loop(
            "bt-state", _loop_row(), _loop_config(TimingStateConfig(confirm_days=2))
        )
        assert self._regimes(svc) == [
            "offensive", "offensive", "offensive", "offensive", "defensive", "defensive", "offensive",
        ]


class TestLiveReplay:
    """实时路径回放最近交易日重建状态。"""

    def _builder(self, history: dict[date, float | None]) -> tuple[ContextBuilder, mock.MagicMock]:
        provider = mock.MagicMock()
        provider.load_market_factor_history.return_value = {
            day: TimingProxySelection(
                values={"t": value},
                factor_proxies={"t": "000300"},
                proxy_index_code="000300" if value is not None else None,
            )
            for day, value in history.items()
        }
        return ContextBuilder(mock.MagicMock(), factor_provider=provider), provider

    def test_replay_rebuilds_state(self) -> None:
        """回放得到上一决策日结束时的状态（确认期内的待切换也被还原）。"""
        days = [date(2025, 1, 6) + timedelta(days=i) for i in range(4)]
        builder, provider = self._builder(dict(zip(days, [90.0, 90.0, 10.0, 10.0])))
        config = _loop_config(TimingStateConfig(confirm_days=3))
        with mock.patch(
            "quant_etf_api.infra.db.repositories.index_daily_bar."
            "IndexDailyBarRepository.find_trading_dates",
            return_value=[*days, TRADE_DATE],
        ):
            state = builder._replay_timing_state(config, TRADE_DATE)
        assert state is not None
        assert state.regime == "offensive"
        assert state.pending == "defensive" and state.pending_days == 2
        # 当日不参与回放
        assert provider.load_market_factor_history.call_args.args[1] == days

    def test_identity_state_skips_replay(self) -> None:
        """择时不依赖历史时不回放、不查交易日。"""
        builder, provider = self._builder({})
        assert builder._replay_timing_state(_loop_config(TimingStateConfig()), TRADE_DATE) is None
        provider.load_market_factor_history.assert_not_called()


# ── 6. 配置校验 ──────────────────────────────────────────────────────────


class _FakeRow:
    """极简 factor_definition 行替身，仅暴露 factor_id。"""

    def __init__(self, factor_id: str) -> None:
        self.factor_id = factor_id


def _config_service() -> StrategyConfigService:
    """构造全部内置模板均已启用的配置服务。"""
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        _FakeRow(fid) for fid in build_default_registry().ids()
    ]
    return StrategyConfigService(db=db)


def _validate(timing: TimingConfig) -> tuple[list[str], list[str]]:
    """校验只改动择时块的策略配置。"""
    config = StrategyConfig(
        strategy_id="v",
        display_name="v",
        score=ScoreConfig(factors={"pe_percentile": 1.0}),
        timing=timing,
    )
    result = _config_service().validate_config(config.model_dump())
    return result.errors, result.warnings


class TestTimingModeValidation:
    """综合方式、规则组与按因子代理的校验。"""

    def test_weighted_requires_factors(self) -> None:
        """加权模式必须配置择时因子。"""
        errors, _w = _validate(TimingConfig())
        assert any("timing.factors 不能为空" in e for e in errors)

    def test_rules_requires_rule(self) -> None:
        """规则模式至少需要一条规则。"""
        errors, _w = _validate(TimingConfig(combine="rules", rules=TimingRulesConfig()))
        assert any("至少配置一条规则" in e for e in errors)

    def test_rules_raw_values_skip_scale_check(self) -> None:
        """规则模式比较原值：非 0-100 的 rsrs 无需变换。"""
        errors, _w = _validate(
            _rules_timing(offensive=FilterConfig(rules=[_rule("rsrs", "gt", 0.7)]))
        )
        assert not any("0-100" in e for e in errors), errors

    def test_rule_group_cross_field_errors(self) -> None:
        """规则组复用过滤规则的跨字段校验，路径标出所在规则组。"""
        errors, _w = _validate(
            _rules_timing(offensive=FilterConfig(rules=[_rule("rsi", "gt", None)]))
        )
        assert any("timing.rules.offensive.rules[0]" in e for e in errors), errors

    def test_rules_mode_warnings(self) -> None:
        """规则模式下 factors / transforms / hysteresis 不生效时给出 warning。"""
        _e, warnings = _validate(
            _rules_timing(
                offensive=FilterConfig(rules=[_rule("rsi", "gt", 50)]),
                factors={"rsi": 1.0},
                transforms={"rsi": "clamp_0_100"},
                state=TimingStateConfig(hysteresis=5),
            )
        )
        assert any("不使用 timing.factors" in w for w in warnings)
        assert any("timing.transforms 不生效" in w for w in warnings)
        assert any("hysteresis" in w for w in warnings)

    def test_factor_proxies_checks(self) -> None:
        """factor_proxies 空列表报错，未使用的因子给 warning。"""
        errors, warnings = _validate(
            TimingConfig(factors={"rsi": 1.0}, factor_proxies={"rsi": [], "other": ["000852"]})
        )
        assert any("timing.factor_proxies.rsi 不能为空列表" in e for e in errors)
        assert any("timing.factor_proxies.other" in w for w in warnings)
