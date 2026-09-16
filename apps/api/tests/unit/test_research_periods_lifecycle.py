"""研究期边界规则与生命周期诊断阈值测试。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from quant_etf_api.domain.research.lifecycle import (
    ACTION_KEEP,
    ACTION_REDUCE_RISK,
    ACTION_RESEARCH,
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
    assess_health,
    build_baseline_distribution,
    evaluate_against_baseline,
)
from quant_etf_api.domain.research.periods import (
    PURPOSE_MONITOR,
    PURPOSE_RESEARCH,
    PURPOSE_VALIDATION,
    PeriodBoundaries,
    validate_backtest_period,
)

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

    @pytest.mark.parametrize("purpose", [PURPOSE_VALIDATION, PURPOSE_MONITOR])
    def test_validation_purposes_may_use_validation_data(self, purpose: str) -> None:
        """验证与监控用途允许使用验证期数据。"""
        validate_backtest_period(
            _BOUNDARIES, purpose, date(2026, 1, 1), date(2026, 9, 13)
        )

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

    def test_monotonic_decay_triggers_warning(self) -> None:
        """超额随窗口单调衰减时判定为 Alpha 衰减。"""
        result = assess_health(30.0, {"3m": 2.0, "6m": 20.0, "12m": 60.0})
        assert result.health_level == HEALTH_WARNING
        assert result.diagnosis == DIAGNOSIS_ALPHA_DECAY
        assert result.recommended_action == ACTION_RESEARCH

    def test_decay_not_confirmed_when_ic_improves(self) -> None:
        """IC 未同步衰减时不应把窗口差异判为 Alpha 衰减。"""
        result = assess_health(
            30.0,
            {"3m": 2.0, "6m": 20.0, "12m": 60.0},
            ic_decay={"first_half_mean": 0.01, "second_half_mean": 0.03},
        )
        assert result.diagnosis == DIAGNOSIS_NORMAL

    def test_decay_confirmed_when_ic_also_decays(self) -> None:
        """超额与 IC 同步衰减时判定为 Alpha 衰减。"""
        result = assess_health(
            30.0,
            {"3m": 2.0, "6m": 20.0, "12m": 60.0},
            ic_decay={"first_half_mean": 0.04, "second_half_mean": 0.01},
        )
        assert result.diagnosis == DIAGNOSIS_ALPHA_DECAY

    def test_decay_detected_when_ic_unavailable(self) -> None:
        """IC 不可用时不否决衰减结论（避免恒假检查屏蔽告警）。"""
        result = assess_health(
            30.0,
            {"3m": 2.0, "6m": 20.0, "12m": 60.0},
            ic_decay={"first_half_mean": None, "second_half_mean": None},
        )
        assert result.diagnosis == DIAGNOSIS_ALPHA_DECAY

    def test_drawdown_plus_decay_is_critical(self) -> None:
        """回撤极端且存在衰减时升级为严重。"""
        result = assess_health(99.0, {"3m": 2.0, "6m": 20.0, "12m": 60.0})
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
