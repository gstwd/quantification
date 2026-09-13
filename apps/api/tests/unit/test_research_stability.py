"""稳健性统计纯函数测试（成本折算、收益集中度、分段一致性与回撤结构）。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from quant_etf_api.domain.research.stability import (
    clean_returns_series,
    compute_stability_metrics,
    net_return_series,
)


def _dates(count: int, start: date = date(2020, 1, 2)) -> list[date]:
    """生成连续自然日序列，仅用于提供年份与顺序。"""
    return [start + timedelta(days=i) for i in range(count)]


class TestCostConversion:
    """成本折算与净口径指标。"""

    def test_net_return_series_subtracts_one_side_turnover_cost(self) -> None:
        """净收益 = 毛收益 − 单边换手 × 成本(bp)/100。"""
        net = net_return_series([1.0, -0.5, 0.5, 0.0], [0.5, 0.0, 1.0, None], cost_bps=10.0)
        assert net == pytest.approx([0.95, -0.5, 0.4, 0.0])

    def test_cost_drag_and_turnover(self) -> None:
        """年化换手与成本拖累按同一口径折算。"""
        metrics = compute_stability_metrics(
            [1.0, -0.5, 0.5, 0.0],
            _dates(4),
            turnovers=[0.5, 0.0, 1.0, None],
            cost_bps=10.0,
        )
        # 4 个交易日累计单边换手 1.5 → 年化 1.5 × 252 / 4 = 94.5 倍
        assert metrics.annualized_turnover == pytest.approx(94.5)
        assert metrics.cost_drag_pct_per_year == pytest.approx(9.45)
        # 净累计收益 = ∏(1 + net/100) − 1
        assert metrics.net_cumulative_return_pct == pytest.approx(0.847, abs=1e-3)

    def test_missing_benchmark_disables_excess(self) -> None:
        """基准序列含缺失时整体作废，不产生超额指标。"""
        metrics = compute_stability_metrics(
            [1.0, 1.0], _dates(2), benchmark_returns=[0.5, None]
        )
        assert metrics.net_excess_return_pct is None

    def test_clean_returns_series_rejects_partial_input(self) -> None:
        """长度不足或含缺失的序列返回 None。"""
        assert clean_returns_series([1.0, None], 2) is None
        assert clean_returns_series([1.0], 2) is None
        assert clean_returns_series([1.0, 2.0], 2) == [1.0, 2.0]


class TestConcentration:
    """收益集中度与剔除最好年份后的表现。"""

    def test_single_year_dominates(self) -> None:
        """收益集中在单一年份时集中度指标应显著。"""
        returns = [10.0, 12.0, -1.0, -2.0]
        dates = [date(2020, 1, 2), date(2020, 1, 3), date(2021, 1, 4), date(2021, 1, 5)]
        metrics = compute_stability_metrics(returns, dates)
        assert metrics.best_year == 2020
        assert metrics.year_return_share_max > 0.85
        # HHI = 0.873² + 0.127² ≈ 0.779
        assert metrics.year_return_share_hhi == pytest.approx(0.779, abs=0.01)
        # 剔除最好年份后只剩亏损，年化应为负
        assert metrics.ex_best_year_annualized_return_pct < 0
        assert metrics.annual_sharpe_positive_ratio == pytest.approx(0.5)

    def test_balanced_years_lower_concentration(self) -> None:
        """两年表现接近时最大占比明显下降。"""
        returns = [5.0, 5.0, -4.0, -4.0]
        dates = [date(2020, 1, 2), date(2020, 1, 3), date(2021, 1, 4), date(2021, 1, 5)]
        metrics = compute_stability_metrics(returns, dates)
        assert metrics.year_return_share_max < 0.6


class TestSegmentsAndDrawdown:
    """分段一致性与回撤结构。"""

    def test_segment_positive_ratio(self) -> None:
        """三段等分区中仅第一段夏普为正时比例为 1/3。"""
        metrics = compute_stability_metrics([1.0, 2.0, -1.0, -2.0, -2.0, -1.0], _dates(6))
        assert metrics.segment_sharpe_positive_ratio == pytest.approx(0.3333)
        assert metrics.best_segment_sharpe > 0
        assert metrics.worst_segment_sharpe < 0

    def test_drawdown_profile(self) -> None:
        """期末回撤分位与最长水下天数按逐日回撤序列统计。"""
        metrics = compute_stability_metrics([10.0, -20.0, 0.0, 0.0], _dates(4))
        assert metrics.max_drawdown_pct == pytest.approx(-20.0)
        assert metrics.current_drawdown_pct == pytest.approx(-20.0)
        # 4 个交易日中有 3 天回撤不浅于当前回撤 → 75 分位
        assert metrics.current_drawdown_percentile_pct == pytest.approx(75.0)
        assert metrics.max_drawdown_days == 3

    def test_position_concentration(self) -> None:
        """持仓集中度取逐日权重 HHI 的均值。"""
        metrics = compute_stability_metrics(
            [0.0, 0.0],
            _dates(2),
            positions=[{"a": 1.0}, {"a": 0.5, "b": 0.5}],
            exposures=[1.0, 1.0],
        )
        assert metrics.position_concentration == pytest.approx(0.75)
        assert metrics.average_exposure == pytest.approx(1.0)

    def test_empty_input_returns_zero_metrics(self) -> None:
        """空序列返回零值指标而不是抛异常。"""
        metrics = compute_stability_metrics([], [])
        assert metrics.annualized_turnover == 0.0
        assert metrics.net_sharpe_ratio == 0.0

    def test_length_mismatch_raises(self) -> None:
        """收益与日期长度不一致时抛 ValueError。"""
        with pytest.raises(ValueError):
            compute_stability_metrics([1.0, 2.0], _dates(1))
