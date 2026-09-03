"""绩效指标口径单元测试（B11：胜率持仓期、夏普全期）。"""

from __future__ import annotations

from datetime import date

import pytest

from quant_etf_api.services.metrics import (
    _calc_alpha_beta,
    _calc_annualized_return,
    _calc_sharpe_ratio,
    _calc_sortino_ratio,
    _calc_win_rate,
    compute_annual_breakdown,
    compute_performance_metrics,
    compute_rolling_metrics,
)


class TestWinRate:
    """胜率口径。"""

    def test_win_rate_uses_active_returns(self) -> None:
        """传入 active_returns 时胜率按持仓日计算，空仓日不进分母。"""
        daily = [1.0, -1.0, 0.0, 0.0, 2.0, 0.0]
        active = [1.0, -1.0, 2.0]
        metrics = compute_performance_metrics(daily, active_returns=active)
        # 持仓日 3 天，正收益 2 天 → 66.67%
        assert metrics.win_rate_pct == 66.67

    def test_win_rate_falls_back_to_daily(self) -> None:
        """未传 active_returns 时退化为全期口径（兼容旧调用）。"""
        daily = [1.0, -1.0, 0.0, 0.0, 2.0, 0.0]
        metrics = compute_performance_metrics(daily)
        # 全期 6 天，正收益 2 天 → 33.33%
        assert metrics.win_rate_pct == 33.33

    def test_calc_win_rate_empty(self) -> None:
        """空序列返回 0。"""
        assert _calc_win_rate([]) == 0.0


class TestSharpe:
    """夏普口径（B11：全期，含空仓日）。"""

    def test_sharpe_unchanged_by_active_returns(self) -> None:
        """传入 active_returns 只影响胜率，不影响夏普/索提诺（全期口径）。"""
        daily = [1.0, -1.0, 0.0, 0.0, 2.0, 0.0]
        active = [1.0, -1.0, 2.0]
        m_full = compute_performance_metrics(daily)
        m_active = compute_performance_metrics(daily, active_returns=active)
        assert m_full.sharpe_ratio == m_active.sharpe_ratio
        assert m_full.sortino_ratio == m_active.sortino_ratio

    def test_empty_returns_metrics_zero(self) -> None:
        """空序列返回零值指标。"""
        m = compute_performance_metrics([], active_returns=[])
        assert m.win_rate_pct == 0.0
        assert m.sharpe_ratio == 0.0
        assert m.total_return_pct == 0.0


class TestRiskFreeRate:
    """无风险利率参数化：夏普/索提诺扣日化 rf，Alpha 同步扣减。"""

    def test_positive_rf_reduces_sharpe_and_sortino(self) -> None:
        """非零无风险利率应同时压低夏普与索提诺。"""
        daily = [0.5, -0.3, 0.8, 0.2, -0.1, 0.6, 0.1, -0.4, 0.9, 0.0]
        m0 = compute_performance_metrics(daily, annual_risk_free_rate_pct=0.0)
        m3 = compute_performance_metrics(daily, annual_risk_free_rate_pct=3.0)
        assert m3.sharpe_ratio < m0.sharpe_ratio
        assert m3.sortino_ratio < m0.sortino_ratio

    def test_rf_zero_keeps_historical_formula(self) -> None:
        """rf=0 时夏普/索提诺与历史实现完全一致（回归保护）。"""
        daily = [0.5, -0.3, 0.8, 0.2, -0.1, 0.6, 0.1, -0.4, 0.9, 0.0]
        m = compute_performance_metrics(daily)
        assert m.sharpe_ratio == pytest.approx(_calc_sharpe_ratio(daily, 252, 0.0))
        assert m.sortino_ratio == pytest.approx(_calc_sortino_ratio(daily, 252, 0.0))

    def test_identical_series_alpha_zero_with_rf(self) -> None:
        """策略与基准完全相同时，任意 rf 下 Alpha=0、Beta=1。"""
        daily = [0.5, -0.3, 0.8, 0.2, -0.1, 0.6, 0.1, -0.4, 0.9, 0.0]
        m = compute_performance_metrics(
            daily,
            benchmark_returns=daily,
            annual_risk_free_rate_pct=3.0,
        )
        assert m.beta == pytest.approx(1.0)
        assert m.alpha == pytest.approx(0.0)

    def test_alpha_formula_deducts_rf(self) -> None:
        """Alpha 按 (Rp-rf)-β(Rm-rf) 计算。"""
        daily = [0.5, -0.3, 0.8, 0.2, -0.1, 0.6, 0.1, -0.4, 0.9, 0.0]
        bench = [0.2, 0.4, -0.2, 0.3, 0.1, -0.1, 0.5, 0.0, -0.3, 0.2]
        alpha, beta = _calc_alpha_beta(daily, bench, 252, annual_risk_free_rate_pct=3.0)

        ann_p = _calc_annualized_return(daily, 252)
        ann_b = _calc_annualized_return(bench, 252)
        expected = (ann_p - 3.0) - beta * (ann_b - 3.0)
        assert alpha == pytest.approx(expected)


class TestRollingMetrics:
    """滚动窗口指标序列。"""

    def test_insufficient_samples_return_none(self) -> None:
        """样本不足完整窗口时对应位置返回 None。"""
        daily = [0.1] * 200
        rolling = compute_rolling_metrics(daily, window=63)
        assert len(rolling) == len(daily)
        assert all(r is None for r in rolling[:62])
        assert all(r is not None for r in rolling[62:])

    def test_window_metrics_equal_full_window_formula(self) -> None:
        """滚动点与直接对窗口切片计算的夏普/索提诺一致。"""
        daily = [0.3, -0.2, 0.5, 0.1, -0.4, 0.6, 0.0, 0.2, -0.1, 0.7] * 30
        rolling = compute_rolling_metrics(daily, window=63)
        sample = daily[-63:]
        point = rolling[-1]
        assert point is not None
        assert point.sharpe_ratio == pytest.approx(_calc_sharpe_ratio(sample, 252))
        assert point.sortino_ratio == pytest.approx(_calc_sortino_ratio(sample, 252))

    def test_window_too_small_raises(self) -> None:
        """窗口小于 2 时抛出 ValueError。"""
        with pytest.raises(ValueError):
            compute_rolling_metrics([0.1], window=1)


class TestAnnualBreakdown:
    """分年度绩效表。"""

    def test_groups_by_calendar_year(self) -> None:
        """按自然年切分，行数与交易日总数一致。"""
        dates_2024 = [date(2024, 1, i + 1) for i in range(30)]
        dates_2025 = [date(2025, 3, i + 1) for i in range(30)]
        daily = [0.1] * 30 + [-0.2] * 30
        rows = compute_annual_breakdown(daily, dates_2024 + dates_2025)
        assert [r.year for r in rows] == [2024, 2025]
        assert rows[0].trading_days == 30
        assert rows[1].trading_days == 30
        assert sum(r.trading_days for r in rows) == len(daily)

    def test_empty_input_returns_empty(self) -> None:
        """空输入返回空列表。"""
        assert compute_annual_breakdown([], []) == []

    def test_length_mismatch_raises(self) -> None:
        """收益与日期长度不一致时抛出 ValueError。"""
        with pytest.raises(ValueError):
            compute_annual_breakdown([0.1, 0.2], [date(2025, 1, 1)])
