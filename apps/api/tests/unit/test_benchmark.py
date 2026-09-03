"""基准收益序列口径单元测试（决策日归因对齐）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from quant_etf_api.services.benchmark import (
    compute_buy_hold_benchmark,
    compute_equal_weight_benchmark,
)
from quant_etf_api.services.metrics import compute_performance_metrics


def _bar(close_price: float) -> SimpleNamespace:
    """构造仅有收盘价的迷你 Bar 行。"""
    return SimpleNamespace(close_price=close_price)


class TestBuyHoldBenchmarkAlignment:
    """买入持有基准按决策日归因（第 i 行为 T_i→T_{i+1} 行情）。"""

    def test_returns_shifted_to_next_day_close_move(self) -> None:
        """基准序列第 i 个元素应为 [收盘 T_i, 收盘 T_{i+1}] 收益，末位记 0。"""
        closes = [100.0, 110.0, 121.0, 120.0]
        dates = [date(2025, 1, i + 2) for i in range(len(closes))]
        bars = {("000300", d): _bar(c) for d, c in zip(dates, closes)}
        series = compute_buy_hold_benchmark(bars, "000300", dates)
        # 10% / 10% / 120÷121-1≈-0.8264% / 末日无下一交易日记 0
        assert series == [10.0, 10.0, pytest.approx(-0.8264, abs=1e-3), 0.0]

    def test_single_date_returns_zero(self) -> None:
        """仅一个交易日时无法计算收益，全部记 0。"""
        bars = {("000300", date(2025, 1, 2)): _bar(100.0)}
        assert compute_buy_hold_benchmark(bars, "000300", [date(2025, 1, 2)]) == [0.0]

    def test_missing_bar_treated_as_zero(self) -> None:
        """行情缺失的相邻交易日收益按 0 处理，不抛异常。"""
        dates = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
        bars = {("000300", dates[0]): _bar(100.0), ("000300", dates[2]): _bar(110.0)}
        series = compute_buy_hold_benchmark(bars, "000300", dates)
        assert series == [0.0, 0.0, 0.0]


class TestEqualWeightBenchmarkAlignment:
    """等权基准与买入持有基准共用同一决策日归因口径。"""

    def test_equal_weight_uses_aligned_intervals(self) -> None:
        """等权基准第 i 个元素应为组合 [收盘 T_i, 收盘 T_{i+1}] 收益。"""
        dates = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
        bars = {
            ("a", dates[0]): _bar(100.0),
            ("a", dates[1]): _bar(110.0),
            ("a", dates[2]): _bar(121.0),
            ("b", dates[0]): _bar(100.0),
            ("b", dates[1]): _bar(100.0),
            ("b", dates[2]): _bar(110.0),
        }
        series = compute_equal_weight_benchmark(bars, ["a", "b"], dates)
        # 第一天：a 涨 10%、b 平 → 5%；第二天：a 涨 10%、b 涨 10% → 10%；末日 0
        assert series == [5.0, 10.0, 0.0]


class TestBenchmarkStrategyAlignment:
    """策略/基准等长配对后，逐日风险指标不再因错位失真。"""

    def test_buy_hold_replica_has_zero_tracking_error(self) -> None:
        """完全复制买入持有基准的策略，跟踪误差应为 0、Beta 应为 1。"""
        closes = [100.0, 101.5, 99.2, 103.4, 102.1, 105.8, 107.2, 106.4]
        dates = [date(2025, 1, i + 2) for i in range(len(closes))]
        bars = {("000300", d): _bar(c) for d, c in zip(dates, closes)}
        benchmark_returns = compute_buy_hold_benchmark(bars, "000300", dates)
        # 策略逐日收益与基准逐日收益完全一致（理想复制场景）
        metrics = compute_performance_metrics(
            list(benchmark_returns),
            benchmark_returns=benchmark_returns,
        )
        assert metrics.beta == pytest.approx(1.0)
        assert metrics.alpha == pytest.approx(0.0)
        assert metrics.tracking_error_pct == pytest.approx(0.0)
        assert metrics.information_ratio == pytest.approx(0.0)
