"""绩效指标口径单元测试（B11：胜率持仓期、夏普全期）。"""

from __future__ import annotations

from quant_etf_api.services.metrics import (
    _calc_win_rate,
    compute_performance_metrics,
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
