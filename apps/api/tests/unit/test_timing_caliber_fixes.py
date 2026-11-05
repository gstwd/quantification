"""择时模块口径修正的回归测试。

覆盖：
- 择时输入量纲：模板声明 native_0_100，未配变换的非原生 0-100 择时因子被校验拒绝；
- 代理一致性：实时与回测共用同一纯函数，优先单一全覆盖代理，否则逐因子回退并标出来源；
- 降级回退：on_degraded=neutral/defensive/hold_previous，回测沿用上一决策日 regime，
  实时无状态退化为 neutral 并告警；回测对部分缺失交易日生成告警；
- timing_exposure 只配部分 regime 时给出 warning，默认三档表只有一个来源。
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from pydantic import ValidationError

from quant_etf_api.domain.strategies.models import TimingSignal
from quant_etf_api.domain.strategies.timing_proxy import (
    TimingProxySelection,
    select_timing_proxy_values,
)
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import (
    FactorAliasConfig,
    PortfolioConfig,
    RankConfig,
    RegimeRuleConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
    TimingConfig,
    TimingThresholds,
)
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.engine.portfolio import _DEFAULT_TIMING_EXPOSURE
from quant_etf_api.engine.score import DefaultScoreCalculator
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.factors.compute import FactorMatrix
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_diagnostics import (
    RunDiagnostics,
    collect_run_warnings,
    timing_partial_warning,
)
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import StrategyConfigService
from quant_etf_api.services.strategy_decision_service import _timing_degraded_warning

TRADE_DATE = date(2025, 1, 15)


class _FakeRow:
    """极简 factor_definition 行替身，仅暴露 factor_id。"""

    def __init__(self, factor_id: str) -> None:
        self.factor_id = factor_id


def _config_service() -> StrategyConfigService:
    """构造全部内置模板均已启用的配置服务（MagicMock 会话驱动真实仓库查询链）。"""
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        _FakeRow(fid) for fid in build_default_registry().ids()
    ]
    return StrategyConfigService(db=db)


def _strategy(**overrides: Any) -> StrategyConfig:
    """构造最小策略配置，允许按关键字覆盖字段。"""
    base: dict[str, Any] = {
        "strategy_id": "t_timing",
        "display_name": "t_timing",
        "score": ScoreConfig(factors={"pe_percentile": 1.0}),
    }
    base.update(overrides)
    return StrategyConfig(**base)


def _context(
    market_factors: dict[str, float | None],
    timing_proxy: TimingProxySelection | None = None,
    previous_timing_regime: str | None = None,
) -> EngineContext:
    """构造只含择时因子的引擎上下文。"""
    return EngineContext(
        trade_date=TRADE_DATE,
        universe=[{"index_code": "A", "name_cn": "A"}],
        asset_factors={("A", "pe_percentile"): 50.0},
        market_factors=market_factors,
        timing_proxy=timing_proxy,
        previous_timing_regime=previous_timing_regime,
    )


# ── 1. 择时输入量纲 ──────────────────────────────────────────────────────


class TestNativeScoreRangeMetadata:
    """模板声明原值是否天然处于 0-100 评分空间。"""

    @pytest.mark.parametrize(
        "template_id",
        ["rsi", "breadth_ma_pct", "pe_percentile", "pb_percentile", "erp_percentile"],
    )
    def test_native_templates(self, template_id: str) -> None:
        """RSI、市场宽度、估值百分位等原值即 0-100。"""
        template = build_default_registry().get(template_id)
        assert template is not None
        assert template.native_0_100 is True
        assert template.spec().native_0_100 is True

    @pytest.mark.parametrize("template_id", ["return", "rsrs", "volume_ratio", "drawdown", "sma"])
    def test_non_native_templates(self, template_id: str) -> None:
        """收益率百分比、z 值、量比、点位等原值不在 0-100 空间。"""
        template = build_default_registry().get(template_id)
        assert template is not None
        assert template.native_0_100 is False

    def test_factor_spec_response_exposes_flag(self) -> None:
        """因子模板接口从代码注册表读取 native_0_100（不落库）。"""
        from quant_etf_api.api.routers.factors import _spec_response

        registry = build_default_registry()
        row = SimpleNamespace(
            factor_id="rsi",
            name="相对强弱指标",
            category="technical",
            version="1.0.0",
            description="",
            required_data=["index_bars"],
            is_active=True,
            value_shape="asset",
            usage=["timing"],
            parameter_schema={},
            default_params={"period": 14},
        )
        assert _spec_response(row, registry).native_0_100 is True
        ghost = SimpleNamespace(**{**row.__dict__, "factor_id": "removed_template"})
        assert _spec_response(ghost, registry).native_0_100 is False


class TestTimingScaleValidation:
    """择时因子未配变换且原值不在 0-100 时校验报错。"""

    def test_raw_return_without_transform_rejected(self) -> None:
        """收益率原值直接混入 0-100 空间会让综合分失真，必须报 error。"""
        config = _strategy(
            timing=TimingConfig(factors={"ret60": 1.0}),
            factor_aliases={
                "ret60": FactorAliasConfig(template_id="return", params={"period": 60})
            },
        )
        result = _config_service().validate_parsed(config)
        assert not result.valid
        assert any("择时因子 ret60" in e and "timing.transforms" in e for e in result.errors)

    def test_raw_rsrs_without_transform_rejected(self) -> None:
        """RSRS 标准分（z 值）不配变换同样报错。"""
        config = _strategy(timing=TimingConfig(factors={"rsrs": 1.0}))
        result = _config_service().validate_parsed(config)
        assert any("择时因子 rsrs" in e for e in result.errors)

    def test_transform_configured_passes(self) -> None:
        """配置了变换函数的非原生因子通过校验。"""
        config = _strategy(
            timing=TimingConfig(factors={"rsrs": 1.0}, transforms={"rsrs": "rsrs_score"})
        )
        result = _config_service().validate_parsed(config)
        assert result.valid, result.errors

    def test_native_factor_without_transform_passes(self) -> None:
        """原生 0-100 因子（含经别名引用）可不配变换。"""
        config = _strategy(
            timing=TimingConfig(factors={"rsi": 0.5, "breadth_fast": 0.5}),
            factor_aliases={
                "breadth_fast": FactorAliasConfig(
                    template_id="breadth_ma_pct", params={"period": 10}
                )
            },
        )
        result = _config_service().validate_parsed(config)
        assert result.valid, result.errors

    def test_score_and_regime_rules_unaffected(self) -> None:
        """评分与 regime 覆盖不做择时量纲校验（regime_rules 不含择时配置）。"""
        config = _strategy(
            score=ScoreConfig(factors={"return_20d": 1.0}),
            timing=TimingConfig(factors={"rsi": 1.0}),
            regime_rules={
                "defensive": RegimeRuleConfig(score=ScoreConfig(factors={"return_20d": 1.0}))
            },
            factor_aliases={
                "return_20d": FactorAliasConfig(template_id="return", params={"period": 20})
            },
        )
        result = _config_service().validate_parsed(config)
        assert result.valid, result.errors

    def test_r60v90_style_timing_unchanged(self) -> None:
        """主策略口径（两个择时因子都配了变换、三档仓位写全）不产生新的错误或提示。"""
        config = _strategy(
            timing=TimingConfig(
                factors={"return_60d": 0.6, "max_drawdown_60d": 0.4},
                transforms={"return_60d": "momentum_score", "max_drawdown_60d": "drawdown_score"},
            ),
            portfolio=PortfolioConfig(
                method="equal_weight",
                timing_exposure={"offensive": 0.9, "neutral": 0.9, "defensive": 0.3},
            ),
            factor_aliases={
                "return_60d": FactorAliasConfig(template_id="return", params={"period": 60}),
                "max_drawdown_60d": FactorAliasConfig(
                    template_id="drawdown", params={"period": 60}
                ),
            },
        )
        result = _config_service().validate_parsed(config)
        assert result.valid, result.errors
        assert not any("timing_exposure" in w for w in result.warnings)


# ── 2. 代理一致性 ────────────────────────────────────────────────────────


class TestSelectTimingProxyValues:
    """代理选择纯函数。"""

    @staticmethod
    def _lookup(table: dict[tuple[str, str], float | None]) -> Any:
        """把字典包装成 (代理, 引用) → 值 的查询函数。"""
        return lambda code, ref: table.get((code, ref))

    def test_first_full_coverage_proxy_wins(self) -> None:
        """首个代理只覆盖部分因子时跳过它，所有因子统一取第一个全覆盖代理。"""
        table = {
            ("000300", "a"): 10.0,
            ("000905", "a"): 20.0,
            ("000905", "b"): 30.0,
        }
        selection = select_timing_proxy_values(
            ["000300", "000905"], ["a", "b"], self._lookup(table)
        )
        assert selection.values == {"a": 20.0, "b": 30.0}
        assert selection.proxy_index_code == "000905"
        assert selection.factor_proxies == {"a": "000905", "b": "000905"}
        assert selection.to_detail() == {"proxy_index_code": "000905", "proxy_mode": "consistent"}

    def test_per_factor_fallback_records_sources(self) -> None:
        """无代理全覆盖时退回逐因子首个有值代理，记录实际来源。"""
        table = {("000300", "a"): 10.0, ("000905", "b"): 30.0}
        selection = select_timing_proxy_values(
            ["000300", "000905"], ["a", "b", "c"], self._lookup(table)
        )
        assert selection.values == {"a": 10.0, "b": 30.0, "c": None}
        assert selection.factor_proxies == {"a": "000300", "b": "000905", "c": None}
        assert selection.mode == "per_factor"
        assert selection.mixed is True

    def test_single_proxy_matches_legacy_caliber(self) -> None:
        """单代理配置：有值取值、缺值为 None。"""
        table = {("000300", "a"): 10.0}
        selection = select_timing_proxy_values(["000300"], ["a", "b"], self._lookup(table))
        assert selection.values == {"a": 10.0, "b": None}
        assert selection.mixed is False

    def test_empty_refs(self) -> None:
        """无择时因子时返回空结果。"""
        selection = select_timing_proxy_values(["000300"], [], self._lookup({}))
        assert selection.values == {}
        assert selection.proxy_index_code is None


class TestBacktestContextProxy:
    """回测上下文与实时共用代理选择纯函数。"""

    def test_backtest_context_uses_consistent_proxy(self) -> None:
        """回测路径按全覆盖代理取值，并携带代理选择结果与上一决策日 regime。"""
        config = _strategy(
            timing=TimingConfig(
                factors={"pe_percentile": 0.5, "pb_percentile": 0.5},
                proxy_index_codes=["000300", "000905"],
            )
        )
        precomputed = {
            ("000300", "pe_percentile"): 20.0,
            ("000905", "pe_percentile"): 45.0,
            ("000905", "pb_percentile"): 55.0,
        }
        builder = ContextBuilder(mock.MagicMock(), factor_provider=mock.MagicMock())

        ctx = builder.build(
            config,
            TRADE_DATE,
            index_codes=["000300"],
            all_bars={},
            precomputed_factors=precomputed,
            previous_timing_regime="defensive",
        )

        assert ctx.market_factors == {"pe_percentile": 45.0, "pb_percentile": 55.0}
        assert ctx.timing_proxy is not None
        assert ctx.timing_proxy.proxy_index_code == "000905"
        assert ctx.previous_timing_regime == "defensive"

    def test_timing_details_mark_proxy(self) -> None:
        """择时明细标出实际使用的代理（汇总字段 + 逐因子来源）。"""
        selection = TimingProxySelection(
            values={"pe_percentile": 20.0, "pb_percentile": 55.0},
            factor_proxies={"pe_percentile": "000300", "pb_percentile": "000905"},
        )
        config = TimingConfig(factors={"pe_percentile": 0.5, "pb_percentile": 0.5})

        _score, _regime, details = DefaultScoreCalculator().calculate_timing(
            config, _context(dict(selection.values), timing_proxy=selection)
        )

        assert details["proxy_index_code"] is None
        assert details["proxy_mode"] == "per_factor"
        assert details["pe_percentile"]["proxy"] == "000300"
        assert details["pb_percentile"]["proxy"] == "000905"


# ── 3. 降级回退可配 ──────────────────────────────────────────────────────


class TestOnDegraded:
    """择时因子全部缺失时的回退 regime。"""

    def test_default_is_neutral(self) -> None:
        """默认 neutral，保持历史口径。"""
        assert TimingConfig(factors={"t": 1.0}).on_degraded == "neutral"

    def test_invalid_policy_rejected(self) -> None:
        """未知回退策略在解析期拒绝。"""
        with pytest.raises(ValidationError):
            TimingConfig(factors={"t": 1.0}, on_degraded="offensive")

    @pytest.mark.parametrize(
        ("policy", "previous", "expected"),
        [
            ("neutral", "offensive", "neutral"),
            ("defensive", None, "defensive"),
            ("hold_previous", "offensive", "offensive"),
            ("hold_previous", "defensive", "defensive"),
            ("hold_previous", None, "neutral"),
        ],
    )
    def test_fallback_regime(self, policy: str, previous: str | None, expected: str) -> None:
        """按 on_degraded 与上一决策日 regime 决定回退 regime。"""
        config = TimingConfig(factors={"t": 1.0}, on_degraded=policy)
        score, regime, details = DefaultScoreCalculator().calculate_timing(
            config, _context({"t": None}, previous_timing_regime=previous)
        )
        assert score == 0.0
        assert regime == expected
        assert details["degraded"] is True
        assert details["on_degraded"] == policy
        assert details["fallback_regime"] == expected
        assert ("fallback_reason" in details) is (policy == "hold_previous" and previous is None)

    def test_defensive_fallback_drives_exposure_with_floor_confidence(self) -> None:
        """defensive 回退按防守档仓位运行，确信度取下限而非由 0 分推出 100%。"""
        config = _strategy(
            timing=TimingConfig(factors={"t": 1.0}, on_degraded="defensive"),
            portfolio=PortfolioConfig(
                method="equal_weight",
                timing_exposure={"offensive": 0.9, "neutral": 0.9, "defensive": 0.3},
            ),
        )
        result = StrategyEngine().run(config, _context({"t": None}), include_details=False)
        assert result.timing is not None
        assert result.timing.regime == "defensive"
        assert result.timing.confidence == 20.0
        assert result.timing_target_exposure == 0.3

    def test_neutral_fallback_confidence_unchanged(self) -> None:
        """neutral 回退的确信度为 20。"""
        config = _strategy(timing=TimingConfig(factors={"t": 1.0}))
        result = StrategyEngine().run(config, _context({"t": None}), include_details=False)
        assert result.timing is not None
        assert result.timing.regime == "neutral"
        assert result.timing.confidence == 20.0


class TestLiveDegradedWarning:
    """实时路径的择时降级结构化告警。"""

    def test_no_warning_when_not_degraded(self) -> None:
        """未降级时不告警。"""
        timing = TimingSignal(regime="offensive", confidence=80.0, label="进攻", factors={})
        assert _timing_degraded_warning(timing, TRADE_DATE) is None
        assert _timing_degraded_warning(None, TRADE_DATE) is None

    def test_hold_previous_falls_back_to_neutral_with_note(self) -> None:
        """实时无状态：hold_previous 退化为 neutral，告警注明原因。"""
        config = TimingConfig(factors={"t": 1.0}, on_degraded="hold_previous")
        _score, regime, details = DefaultScoreCalculator().calculate_timing(
            config, _context({"t": None})
        )
        timing = TimingSignal(regime=regime, confidence=20.0, label="观望", factors=details)

        warning = _timing_degraded_warning(timing, TRADE_DATE)

        assert warning is not None
        assert warning.code == "TIMING_DEGRADED"
        assert warning.trade_date == TRADE_DATE
        assert "中性" in warning.message
        assert "on_degraded=hold_previous" in warning.message
        assert "退化为 neutral" in warning.message


class TestBacktestTimingWarnings:
    """回测的择时降级与部分缺失告警。"""

    def test_neutral_degraded_message_unchanged(self) -> None:
        """默认策略（neutral 回退）的 TIMING_DEGRADED 文案。"""
        warnings = collect_run_warnings(
            RunDiagnostics(timing_degraded_dates=[date(2025, 1, 2), date(2025, 1, 3)])
        )
        degraded = [w for w in warnings if w.code == "TIMING_DEGRADED"]
        assert len(degraded) == 1
        assert degraded[0].message == (
            "择时因子在 2 个交易日全部缺失（2025-01-02~2025-01-03），"
            "这些交易日按中性 regime 决定总仓位"
        )

    @pytest.mark.parametrize(
        ("policy", "fragment"),
        [("defensive", "按防守 regime"), ("hold_previous", "沿用上一决策日的 regime")],
    )
    def test_degraded_message_follows_policy(self, policy: str, fragment: str) -> None:
        """TIMING_DEGRADED 文案反映实际回退策略。"""
        warnings = collect_run_warnings(
            RunDiagnostics(timing_degraded_dates=[date(2025, 1, 2)], timing_on_degraded=policy)
        )
        message = next(w.message for w in warnings if w.code == "TIMING_DEGRADED")
        assert fragment in message

    def test_partial_missing_warning(self) -> None:
        """部分缺失交易日生成 TIMING_PARTIAL_MISSING，按缺失天数列出因子。"""
        warning = timing_partial_warning([date(2025, 1, 2), date(2025, 1, 6)], {"b": 1, "a": 2})
        assert warning is not None
        assert warning.code == "TIMING_PARTIAL_MISSING"
        assert "2 个交易日部分缺失" in warning.message
        assert "a 2 天、b 1 天" in warning.message
        assert timing_partial_warning([], {}) is None


# ── 回测主循环接线 ───────────────────────────────────────────────────────

# 2025-01-06（周一）至 01-10（周五）：避开周末，否则会被当作非交易日行情剔除
LOOP_DATES = [date(2025, 1, 6) + timedelta(days=i) for i in range(5)]


def _loop_service(timing_values: list[tuple[float | None, float | None]]) -> BacktestService:
    """构造使用真实上下文构建与真实引擎的内存回测服务。

    Args:
        timing_values: 每个交易日代理指数上两个择时因子 (t, u) 的取值。

    Returns:
        行情、因子与仓库均为内存桩的 BacktestService。
    """
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
    values: dict[date, dict[tuple[str, str], float | None]] = {}
    for d, (t, u) in zip(LOOP_DATES, timing_values):
        values[d] = {
            ("000300", "val"): 1.0,
            ("000905", "val"): 2.0,
            ("000300", "t"): t,
            ("000300", "u"): u,
        }
    svc._factor_provider = mock.MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = FactorMatrix(values=values)
    svc._context_builder = ContextBuilder(mock.MagicMock(), factor_provider=svc._factor_provider)
    svc._engine = StrategyEngine()
    svc._backtest_repo = mock.MagicMock()
    return svc


def _loop_row() -> BacktestRunModel:
    """构造运行中的回测行。"""
    return BacktestRunModel(
        backtest_id="bt-timing",
        strategy_id="t_timing",
        start_date=LOOP_DATES[0],
        end_date=LOOP_DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300", "000905"]},
        params={"_execution_model": "t_plus_1_open", "_data_quality_mode": "warn"},
        status="running",
    )


def _loop_config(on_degraded: str) -> StrategyConfig:
    """两个择时因子、原值即 0-100 口径的最小回测配置。"""
    return StrategyConfig(
        strategy_id="t_timing",
        display_name="t_timing",
        index_codes=["000300", "000905"],
        timing=TimingConfig(
            factors={"t": 1.0, "u": 1.0},
            thresholds=TimingThresholds(offensive=65, defensive=35),
            on_degraded=on_degraded,
        ),
        score=ScoreConfig(factors={"val": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight"),
        risk=RiskConfig(max_asset_weight=1.0),
    )


class TestBacktestLoopTiming:
    """回测主循环：hold_previous 沿用上一决策日 regime，部分缺失生成告警。"""

    # 第 1 日进攻；第 2 日全部缺失（降级）；第 3 日只缺 u（部分缺失，按 t=90 进攻）；
    # 第 4 日防守；第 5 日为最后一个交易日（无收益窗口，不形成决策）
    _VALUES: list[tuple[float | None, float | None]] = [
        (90.0, 90.0),
        (None, None),
        (90.0, None),
        (10.0, 10.0),
        (10.0, 10.0),
    ]

    @staticmethod
    def _regimes(svc: BacktestService) -> list[str | None]:
        """按交易日顺序取落库的逐日 regime。"""
        return [
            call.args[0].timing_regime
            for call in svc._backtest_repo.add_daily_result.call_args_list
        ]

    def test_hold_previous_uses_previous_decision_day_regime(self) -> None:
        """降级日沿用上一决策日的 regime，并在告警中说明回退策略。"""
        svc = _loop_service(self._VALUES)
        svc._run_backtest_loop("bt-timing", _loop_row(), _loop_config("hold_previous"))

        assert self._regimes(svc) == ["offensive", "offensive", "offensive", "defensive"]
        warnings = svc._backtest_repo.mark_success.call_args.kwargs["warnings"]
        degraded = next(w for w in warnings if w["code"] == "TIMING_DEGRADED")
        assert "1 个交易日全部缺失" in degraded["message"]
        assert "沿用上一决策日的 regime" in degraded["message"]

    def test_default_neutral_and_partial_missing_warning(self) -> None:
        """默认 neutral 回退口径不变；部分缺失交易日也生成告警。"""
        svc = _loop_service(self._VALUES)
        svc._run_backtest_loop("bt-timing", _loop_row(), _loop_config("neutral"))

        assert self._regimes(svc) == ["offensive", "neutral", "offensive", "defensive"]
        warnings = svc._backtest_repo.mark_success.call_args.kwargs["warnings"]
        codes = [w["code"] for w in warnings]
        assert "TIMING_DEGRADED" in codes
        partial = next(w for w in warnings if w["code"] == "TIMING_PARTIAL_MISSING")
        assert "1 个交易日部分缺失" in partial["message"]
        assert "u 1 天" in partial["message"]


# ── 4. timing_exposure 部分配置 ──────────────────────────────────────────


class TestPartialTimingExposure:
    """timing_exposure 只配置了部分 regime 时给出 warning。"""

    def test_partial_top_level_warns(self) -> None:
        """顶层组合缺档时提示缺失档位会回退到 default_exposure。"""
        config = _strategy(
            timing=TimingConfig(factors={"rsi": 1.0}),
            portfolio=PortfolioConfig(
                method="equal_weight",
                timing_exposure={"offensive": 0.9, "neutral": 0.6},
            ),
        )
        _errors, warnings = StrategyConfigService._structural_validation(config)
        hits = [w for w in warnings if w.startswith("portfolio.timing_exposure 缺少")]
        assert len(hits) == 1
        assert "defensive（默认三档表为 20%）" in hits[0]
        assert "default_exposure=50%" in hits[0]

    def test_full_or_absent_does_not_warn(self) -> None:
        """三档写全或未配置 timing_exposure（使用默认三档表）时不提示。"""
        for exposure in ({"offensive": 0.8, "neutral": 0.5, "defensive": 0.2}, None):
            config = _strategy(
                timing=TimingConfig(factors={"rsi": 1.0}),
                portfolio=PortfolioConfig(method="equal_weight", timing_exposure=exposure),
            )
            _errors, warnings = StrategyConfigService._structural_validation(config)
            assert not any("timing_exposure 缺少" in w for w in warnings)

    def test_no_warning_without_timing(self) -> None:
        """未配置择时时 timing_exposure 不生效，不做缺档提示。"""
        config = _strategy(
            portfolio=PortfolioConfig(method="equal_weight", timing_exposure={"offensive": 0.9})
        )
        _errors, warnings = StrategyConfigService._structural_validation(config)
        assert not any("timing_exposure 缺少" in w for w in warnings)

    def test_regime_block_checks_only_its_own_regime(self) -> None:
        """regime 覆盖块只在本 regime 下读取仓位，只检查本 regime 是否缺档。"""
        config = _strategy(
            timing=TimingConfig(factors={"rsi": 1.0}),
            regime_rules={
                "defensive": RegimeRuleConfig(
                    portfolio=PortfolioConfig(
                        method="equal_weight", timing_exposure={"defensive": 0.1}
                    )
                ),
                "offensive": RegimeRuleConfig(
                    portfolio=PortfolioConfig(
                        method="equal_weight", timing_exposure={"neutral": 0.5}
                    )
                ),
            },
        )
        _errors, warnings = StrategyConfigService._structural_validation(config)
        hits = [w for w in warnings if "timing_exposure 缺少" in w]
        assert len(hits) == 1
        assert hits[0].startswith("regime_rules.offensive.portfolio.timing_exposure 缺少 offensive")

    def test_winner_take_all_uses_engine_default_table(self) -> None:
        """赢家通吃裁剪提示与引擎默认三档表同源（未配置 timing_exposure 时）。"""
        config = _strategy(
            timing=TimingConfig(factors={"rsi": 1.0}),
            portfolio=PortfolioConfig(method="winner_take_all"),
            risk=RiskConfig(max_asset_weight=0.5),
        )
        _errors, warnings = StrategyConfigService._structural_validation(config)
        top = max(_DEFAULT_TIMING_EXPOSURE.values())
        assert any(f"目标仓位最高 {top:.0%}" in w for w in warnings)
