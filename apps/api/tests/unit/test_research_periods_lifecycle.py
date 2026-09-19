"""研究期边界规则与生命周期诊断阈值测试。"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from quant_etf_api.domain.research.lifecycle import (
    ACTION_KEEP,
    ACTION_REDUCE_RISK,
    ACTION_WATCH,
    DIAGNOSIS_ALPHA_DECAY,
    DIAGNOSIS_DRAWDOWN_EXTREME,
    DIAGNOSIS_INSUFFICIENT_DATA,
    DIAGNOSIS_NORMAL,
    HEALTH_CRITICAL,
    HEALTH_HEALTHY,
    HEALTH_UNKNOWN,
    HEALTH_WARNING,
    HEALTH_WATCH,
    SCORE_IC_DECAY_MIN_HALF_N,
    assess_health,
    build_baseline_distribution,
    evaluate_against_baseline,
    has_ic_decay_evidence,
    ic_decay_evidence,
    ic_evidence_shortfall,
)
from quant_etf_api.domain.research.periods import (
    PURPOSE_MONITOR,
    PURPOSE_RESEARCH,
    PURPOSE_VALIDATION,
    PeriodBoundaries,
    validate_backtest_period,
)
from quant_etf_api.services.strategy_lifecycle_service import _selection_forward_days

_BOUNDARIES = PeriodBoundaries(
    research_start=date(2016, 1, 1),
    research_end=date(2025, 12, 31),
    validation_start=date(2026, 1, 1),
)


def _series(count: int, mean: float, sigma: float = 0.01, seed: int = 99) -> list[float]:
    """构造确定性日收益序列（百分比口径）。"""
    values: list[float] = []
    state = seed
    for _ in range(count):
        state = (1103515245 * state + 12345) % (2**31)
        uniform = state / (2**31)
        values.append((mean + sigma * (uniform * 2 - 1)) * 100)
    return values


def _dates(count: int, start: date = date(2020, 1, 2)) -> list[date]:
    """生成连续自然日序列（仅用于提供顺序与区间）。"""
    return [start + timedelta(days=i) for i in range(count)]


class TestPeriodRules:
    """研究期 / 验证期边界规则。"""

    def test_research_cannot_cross_research_end(self) -> None:
        """研究类回测越过研究期末端应被拒绝。"""
        with pytest.raises(ValueError) as exc:
            validate_backtest_period(
                _BOUNDARIES, PURPOSE_RESEARCH, date(2025, 1, 1), date(2026, 1, 5)
            )
        assert "验证期" in str(exc.value)

    def test_research_within_period_allowed(self) -> None:
        """研究期内区间正常通过。"""
        validate_backtest_period(
            _BOUNDARIES, PURPOSE_RESEARCH, date(2016, 1, 1), date(2025, 12, 31)
        )

    def test_research_cannot_start_before_research_period(self) -> None:
        with pytest.raises(ValueError) as exc:
            validate_backtest_period(
                _BOUNDARIES, PURPOSE_RESEARCH, date(2015, 12, 31), date(2025, 12, 31)
            )
        assert "研究期起点" in str(exc.value)

    @pytest.mark.parametrize("purpose", [PURPOSE_VALIDATION, PURPOSE_MONITOR])
    def test_validation_purposes_may_use_validation_data(self, purpose: str) -> None:
        """验证与监控用途允许使用验证期数据。"""
        validate_backtest_period(
            _BOUNDARIES, purpose, date(2026, 1, 1), date(2026, 9, 13)
        )

    @pytest.mark.parametrize("purpose", [PURPOSE_VALIDATION, PURPOSE_MONITOR])
    def test_validation_purposes_reject_research_dates(self, purpose: str) -> None:
        with pytest.raises(ValueError) as exc:
            validate_backtest_period(
                _BOUNDARIES, purpose, date(2025, 12, 31), date(2026, 1, 5)
            )
        assert "验证期起点" in str(exc.value)

    def test_unknown_purpose_raises(self) -> None:
        """未知用途直接拒绝。"""
        with pytest.raises(ValueError):
            validate_backtest_period(_BOUNDARIES, "unknown", date(2020, 1, 1), date(2020, 2, 1))

    def test_reversed_dates_raise(self) -> None:
        """起始日期晚于截止日期时拒绝。"""
        with pytest.raises(ValueError):
            validate_backtest_period(
                _BOUNDARIES, PURPOSE_RESEARCH, date(2024, 1, 1), date(2023, 1, 1)
            )


class TestBaselineDistribution:
    """研究期分布快照与上线后分位定位。"""

    def test_distribution_covers_available_windows(self) -> None:
        """样本长度决定可用窗口：300 天覆盖 1M/3M/6M/12M，不覆盖 24M。"""
        returns = _series(300, 0.0004)
        snapshot = build_baseline_distribution(returns, None, _dates(300))
        assert snapshot["sample_days"] == 300
        assert set(snapshot["windows"]) == {"1m", "3m", "6m", "12m"}
        assert snapshot["windows"]["1m"]["n_observations"] == 300 - 21 + 1
        assert snapshot["drawdown_pct"]["50"] >= 0
        assert snapshot["period"]["start"] == "2020-01-02"

    def test_short_live_sample_only_fills_short_windows(self) -> None:
        """上线样本不足 6M 时只有 1M/3M 窗口可用，其余标记样本不足。"""
        research = _series(300, 0.0004)
        snapshot = build_baseline_distribution(research, None, _dates(300))
        live = _series(70, 0.0004, seed=7)
        evaluation = evaluate_against_baseline(live, None, snapshot)
        assert evaluation["windows"]["1m"]["available"] is True
        assert evaluation["windows"]["3m"]["available"] is True
        assert evaluation["windows"]["6m"]["available"] is False
        assert evaluation["windows"]["12m"]["available"] is False
        percentile = evaluation["windows"]["3m"]["excess_return_percentile_pct"]
        assert percentile is not None and 0.0 <= percentile <= 100.0
        assert evaluation["windows"]["3m"]["expectation_gap_pct"] is not None

    def test_empty_research_series(self) -> None:
        """研究期无数据时返回空快照而不是抛异常。"""
        snapshot = build_baseline_distribution([], None, [])
        assert snapshot["sample_days"] == 0
        assert snapshot["windows"] == {}


class TestHealthAssessment:
    """健康等级判定（阈值全部取自策略自身历史分布）。"""

    def test_normal_when_within_distribution(self) -> None:
        """分位处于正常范围时判定健康。"""
        result = assess_health(40.0, {"3m": 55.0, "6m": 60.0, "12m": 45.0})
        assert result.health_level == HEALTH_HEALTHY
        assert result.diagnosis == DIAGNOSIS_NORMAL
        assert result.recommended_action == ACTION_KEEP

    def test_extreme_drawdown_triggers_warning(self) -> None:
        """当前回撤超过研究期 95 分位时告警并建议降低风险。"""
        result = assess_health(99.0, {"3m": 55.0})
        assert result.health_level == HEALTH_WARNING
        assert result.diagnosis == DIAGNOSIS_DRAWDOWN_EXTREME
        assert result.recommended_action == ACTION_REDUCE_RISK

    def test_monotonic_decay_without_ic_triggers_watch(self) -> None:
        """收益衰减但没有 IC 证据时只进入观察。"""
        result = assess_health(30.0, {"3m": 2.0, "6m": 20.0, "12m": 60.0})
        assert result.health_level == HEALTH_WATCH
        assert result.diagnosis == DIAGNOSIS_ALPHA_DECAY
        assert result.recommended_action == ACTION_WATCH

    def test_decay_not_confirmed_when_ic_improves(self) -> None:
        """IC 未同步衰减时不应把窗口差异判为 Alpha 衰减。"""
        result = assess_health(
            30.0,
            {"3m": 2.0, "6m": 20.0, "12m": 60.0},
            composite_ic_decay={"first_half_mean": 0.01, "second_half_mean": 0.03},
        )
        assert result.health_level == HEALTH_WATCH
        assert result.diagnosis == DIAGNOSIS_ALPHA_DECAY

    def test_decay_confirmed_when_ic_also_decays(self) -> None:
        """超额与 IC 同步衰减时判定为 Alpha 衰减。"""
        result = assess_health(
            30.0,
            {"3m": 2.0, "6m": 20.0, "12m": 60.0},
            composite_ic_decay={"first_half_mean": 0.04, "second_half_mean": 0.01},
        )
        assert result.diagnosis == DIAGNOSIS_ALPHA_DECAY

    def test_decay_without_ic_is_not_confirmed(self) -> None:
        """IC 不可用时收益衰减不能升级为确认的 Alpha 衰减告警。"""
        result = assess_health(
            30.0,
            {"3m": 2.0, "6m": 20.0, "12m": 60.0},
            composite_ic_decay={"first_half_mean": None, "second_half_mean": None},
        )
        assert result.health_level == HEALTH_WATCH

    def test_drawdown_plus_confirmed_decay_is_critical(self) -> None:
        """回撤极端且收益、IC 均衰减时升级为严重。"""
        result = assess_health(
            99.0,
            {"3m": 2.0, "6m": 20.0, "12m": 60.0},
            composite_ic_decay={"first_half_mean": 0.04, "second_half_mean": 0.01},
        )
        assert result.health_level == HEALTH_CRITICAL
        assert result.diagnosis == DIAGNOSIS_ALPHA_DECAY

    def test_consecutive_low_alpha_triggers_watch(self) -> None:
        """3M 超额分位连续两次低于 5 分位时进入观察。"""
        result = assess_health(
            30.0, {"3m": 2.0}, trailing_alpha_percentiles=[3.0]
        )
        assert result.health_level == HEALTH_WATCH
        assert result.recommended_action == ACTION_WATCH

    def test_single_low_alpha_not_enough(self) -> None:
        """只出现一次低分位不足以下结论。"""
        result = assess_health(30.0, {"3m": 2.0}, trailing_alpha_percentiles=[])
        assert result.health_level == HEALTH_HEALTHY

    def test_insufficient_data_is_reported(self) -> None:
        """所有窗口样本不足时明确标注样本不足，且不得记为 HEALTHY。"""
        result = assess_health(None, {"1m": None, "3m": None, "6m": None})
        assert result.diagnosis == DIAGNOSIS_INSUFFICIENT_DATA
        assert result.recommended_action == ACTION_KEEP
        # "没算出问题" ≠ "没有问题"：等级必须是 UNKNOWN
        assert result.health_level == HEALTH_UNKNOWN


class TestIcDecayEvidence:
    """测试 IC 衰减的显著性判定（服务层据此外推 confirmed）。"""

    def test_significant_decay_is_confirmed(self) -> None:
        """前半段明显高于后半段（差值远超 2 倍标准误）时确认衰减。"""
        values = [0.10, 0.12, 0.08, 0.11, 0.09] * 8 + [0.01, 0.00, 0.02, -0.01, 0.01] * 8
        evidence = ic_decay_evidence(values)
        assert evidence["confirmed"] is True
        assert evidence["delta"] > 0
        assert evidence["first_half_n"] == 40
        assert evidence["second_half_n"] == 40

    def test_noise_difference_is_not_confirmed(self) -> None:
        """前后半段均值差远小于标准误时不能判为衰减。"""
        values = [0.02, -0.30, 0.35, -0.25, 0.30, -0.32, 0.28, 0.01] * 10
        evidence = ic_decay_evidence(values)
        assert evidence["confirmed"] is False

    def test_improving_ic_is_not_confirmed(self) -> None:
        """后半段更高时明确不确认衰减。"""
        values = [0.01, 0.00, 0.02, -0.01, 0.01] * 8 + [0.10, 0.12, 0.08, 0.11, 0.09] * 8
        evidence = ic_decay_evidence(values)
        assert evidence["confirmed"] is False
        assert evidence["delta"] < 0

    def test_too_few_observations_returns_no_evidence(self) -> None:
        """半段观测不足时不给均值也不确认衰减。"""
        evidence = ic_decay_evidence([0.05] * 20)
        assert evidence["confirmed"] is False
        assert evidence["first_half_mean"] is None
        assert evidence["second_half_mean"] is None
        assert evidence["delta"] is None

    def test_empty_series_is_safe(self) -> None:
        """空序列不抛异常。"""
        evidence = ic_decay_evidence([])
        assert evidence["confirmed"] is False
        assert evidence["first_half_n"] == 0


class TestIcDecayOverlapHandling:
    """前瞻窗口重叠时必须抽非重叠子样本，否则门槛与标准误同时失真。"""

    @staticmethod
    def _decaying(n: int) -> list[float]:
        """构造前高后低的 IC 序列（带微小波动，避免半段方差为 0）。

        周期长度取 7（与 overlap_step=5 互质），保证抽样后两个半段仍保留波动，
        否则标准误恒为 0，"显著衰减"的判定会被退化掉。
        """
        high = (0.10, 0.12, 0.08, 0.11, 0.09, 0.13, 0.07)
        low = (0.01, -0.01, 0.02, 0.00, 0.03, -0.02, 0.01)
        half = n // 2
        return [high[i % len(high)] for i in range(half)] + [
            low[i % len(low)] for i in range(n - half)
        ]

    def test_overlap_step_reduces_effective_sample(self) -> None:
        """周频策略的原始 169 个观测量不足以支撑判定：非重叠只有 34 个。"""
        evidence = ic_decay_evidence(self._decaying(169), overlap_step=5)
        assert evidence["observed_n"] == 169
        assert evidence["effective_n"] == 34
        # 低于 IC_DECAY_MIN_HALF_N × 2 = 40，必须承认没有证据
        assert evidence["first_half_mean"] is None
        assert evidence["confirmed"] is False

    def test_overlap_step_at_threshold_still_usable(self) -> None:
        """非重叠观测恰好达到门槛时正常给出证据。"""
        evidence = ic_decay_evidence(self._decaying(200), overlap_step=5)
        assert evidence["effective_n"] == 40
        assert evidence["first_half_mean"] == pytest.approx(0.10, abs=0.005)
        assert evidence["second_half_mean"] == pytest.approx(0.006, abs=0.01)
        assert evidence["confirmed"] is True

    def test_step_one_matches_legacy_behaviour(self) -> None:
        """前瞻期为 1 天（日频）时序列本身非重叠，结果与改造前一致。"""
        values = self._decaying(80)
        assert ic_decay_evidence(values) == ic_decay_evidence(values, overlap_step=1)

    def test_invalid_step_falls_back_to_one(self) -> None:
        """非法步长按 1 处理，不能除零或越界切片。"""
        evidence = ic_decay_evidence(self._decaying(80), overlap_step=0)
        assert evidence["overlap_step"] == 1
        assert evidence["effective_n"] == 80

    def test_score_level_threshold_allows_shorter_rebalance_series(self) -> None:
        """组合序列用更低的门槛，否则周频策略要攒 40 个调仓日才可能出证据。"""
        values = self._decaying(24)
        strict = ic_decay_evidence(values)
        assert strict["min_required_n"] == 40
        assert strict["first_half_mean"] is None

        score_level = ic_decay_evidence(values, min_half_n=SCORE_IC_DECAY_MIN_HALF_N)
        assert score_level["min_required_n"] == 24
        assert score_level["effective_n"] == 24
        assert score_level["first_half_mean"] == pytest.approx(0.10, abs=0.005)
        assert score_level["confirmed"] is True

    def test_score_level_threshold_still_blocks_tiny_series(self) -> None:
        """门槛降低不等于没有门槛：12 个观测依然拿不到证据。"""
        evidence = ic_decay_evidence(
            self._decaying(12), min_half_n=SCORE_IC_DECAY_MIN_HALF_N
        )
        assert evidence["min_required_n"] == 24
        assert evidence["first_half_mean"] is None
        assert evidence["confirmed"] is False


class TestIcEvidenceShortfall:
    """把"还差多少证据"折算成可预期的时间表。"""

    def test_weekly_series_reports_remaining_observations_and_months(self) -> None:
        """周频策略 34 个调仓日离 24 个门槛已达标，不应再报缺口。"""
        shortfall = ic_evidence_shortfall(34, forward_days=5)
        assert shortfall == {"required_n": 24, "shortfall_n": 0, "shortfall_months": 0}

    def test_monthly_series_reports_multi_year_gap(self) -> None:
        """月频策略 8 个调仓日还差 16 个，约 16 个月。"""
        shortfall = ic_evidence_shortfall(8, forward_days=21)
        assert shortfall["required_n"] == 24
        assert shortfall["shortfall_n"] == 16
        assert shortfall["shortfall_months"] == 16

    def test_zero_observations_reports_full_gap(self) -> None:
        """一个观测都没有时要报出完整缺口而不是 0。"""
        shortfall = ic_evidence_shortfall(0, forward_days=1)
        assert shortfall["shortfall_n"] == 24
        assert shortfall["shortfall_months"] == 2

    def test_negative_or_invalid_inputs_are_safe(self) -> None:
        """负观测数与 0 交易日步长都不能产生负数或除零。"""
        shortfall = ic_evidence_shortfall(-5, forward_days=0)
        assert shortfall["shortfall_n"] == 24
        assert shortfall["shortfall_months"] == 2


class TestHasIcDecayEvidence:
    """证据可用性与"是否确认衰减"必须区分开。"""

    def test_missing_evidence_is_not_usable(self) -> None:
        assert has_ic_decay_evidence(None) is False
        assert has_ic_decay_evidence({}) is False
        assert has_ic_decay_evidence({"first_half_mean": None, "second_half_mean": None}) is False

    def test_negative_conclusion_still_counts_as_evidence(self) -> None:
        """算出了均值但未确认衰减，仍属于"有证据的阴性结论"。"""
        evidence = ic_decay_evidence([0.02, -0.30, 0.35, -0.25, 0.30, -0.32, 0.28, 0.01] * 10)
        assert evidence["confirmed"] is False
        assert has_ic_decay_evidence(evidence) is True


class TestConfirmedFlagIsAuthoritative:
    """测试 assess_health 对 confirmed 标记的采纳优先级。"""

    def test_confirmed_false_overrides_lower_second_half(self) -> None:
        """服务层判定不显著时，即使后半段均值更低也不得升级为告警。"""
        result = assess_health(
            30.0,
            {"3m": 2.0, "6m": 20.0, "12m": 60.0},
            composite_ic_decay={
                "first_half_mean": 0.04,
                "second_half_mean": 0.01,
                "confirmed": False,
            },
        )
        assert result.health_level == HEALTH_WATCH
        assert result.diagnosis == DIAGNOSIS_ALPHA_DECAY

    def test_confirmed_true_overrides_higher_second_half(self) -> None:
        """confirmed 为真时以其为准（服务层已做过显著性检验）。"""
        result = assess_health(
            99.0,
            {"3m": 2.0, "6m": 20.0, "12m": 60.0},
            composite_ic_decay={
                "first_half_mean": 0.01,
                "second_half_mean": 0.03,
                "confirmed": True,
            },
        )
        assert result.health_level == HEALTH_CRITICAL
        assert result.diagnosis == DIAGNOSIS_ALPHA_DECAY


class TestSelectionForwardDays:
    """组合分数 IC 必须对齐实际选股调仓频率。"""

    @staticmethod
    def _config(frequency: str) -> SimpleNamespace:
        return SimpleNamespace(
            rebalance=SimpleNamespace(selection=SimpleNamespace(frequency=frequency))
        )

    def test_daily_weekly_monthly_mapping(self) -> None:
        assert _selection_forward_days(self._config("daily")) == 1
        assert _selection_forward_days(self._config("weekly")) == 5
        assert _selection_forward_days(self._config("monthly")) == 21

    def test_biweekly_and_missing_config_are_safe(self) -> None:
        assert _selection_forward_days(self._config("biweekly")) == 10
        assert _selection_forward_days(None) == 1


class TestHealthAssessmentIcEvidenceReporting:
    """IC 证据缺失必须在 reasons 中说明，不能被读成一次正常的阴性结论。"""

    _DECAY_WINDOWS = {"3m": 2.0, "6m": 20.0, "12m": 60.0}

    def test_absent_ic_evidence_is_reported(self) -> None:
        """完全没传 IC 证据时说明未采用，而不是默认"没问题"。"""
        result = assess_health(30.0, self._DECAY_WINDOWS)
        assert result.health_level == HEALTH_WATCH
        assert any("未采用 IC 证据" in reason for reason in result.reasons)

    def test_insufficient_ic_evidence_is_reported(self) -> None:
        """非重叠观测不足时均值为 None，同样要说明未采用。"""
        result = assess_health(
            30.0,
            self._DECAY_WINDOWS,
            composite_ic_decay={"first_half_mean": None, "second_half_mean": None},
        )
        assert result.health_level == HEALTH_WATCH
        assert any("未采用 IC 证据" in reason for reason in result.reasons)

    def test_available_evidence_uses_distinct_wording(self) -> None:
        """算出了均值但未确认衰减时，措辞必须是"证据不足"而非"未采用证据"。"""
        result = assess_health(
            30.0,
            self._DECAY_WINDOWS,
            composite_ic_decay={
                "first_half_mean": 0.04,
                "second_half_mean": 0.01,
                "confirmed": False,
            },
        )
        assert result.health_level == HEALTH_WATCH
        assert any("衰减证据不足" in reason for reason in result.reasons)
        assert not any("未采用 IC 证据" in reason for reason in result.reasons)
