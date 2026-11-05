"""收益归因与混合基准测试。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest
from pydantic import ValidationError

from quant_etf_api.domain.portfolio.attribution import AttributionDay, compute_attribution
from quant_etf_api.domain.portfolio.benchmark import (
    compute_buy_hold_benchmark,
    compute_mix_benchmark,
)
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.services.backtest_service import BacktestService, IndexDetailPrunedError

_D = [date(2025, 3, 3) + timedelta(days=i) for i in range(4)]


class TestAttributionDomain:
    """逐日恒等式与汇总。"""

    def test_identity_and_assets(self) -> None:
        """r_p − r_b = 择时 + 选择 + 残差；资产贡献按 w·r 累加。"""
        days = [
            AttributionDay(_D[0], 0.8, {"A": 0.5, "B": 0.3}, benchmark_return=1.0),
            AttributionDay(_D[1], -0.5, {"A": 0.5}, benchmark_return=-2.0),
        ]
        returns = {(_D[0], "A"): 1.0, (_D[0], "B"): 1.0, (_D[1], "A"): -1.0}
        result = compute_attribution(days, returns)
        total = result.total
        assert total.days == 2
        assert total.timing_pct == pytest.approx((0.8 - 1) * 1.0 + (0.5 - 1) * -2.0)
        assert total.selection_pct == pytest.approx((0.8 - 0.8) + (-0.5 + 1.0))
        assert total.residual_pct == pytest.approx(0.0)
        gap = total.portfolio_pct - (total.benchmark_pct or 0)
        assert gap == pytest.approx(total.timing_pct + total.selection_pct + total.residual_pct)
        assert [a.index_code for a in result.assets] == ["B", "A"]
        assert result.assets[1].held_days == 2
        assert result.assets[1].win_rate == pytest.approx(0.5)

    def test_missing_return_goes_to_residual_and_no_benchmark(self) -> None:
        """持有资产缺收益计入残差；无基准时不出择时 / 选择。"""
        days = [AttributionDay(_D[0], 1.0, {"A": 1.0})]
        result = compute_attribution(days, {})
        assert result.total.residual_pct == pytest.approx(1.0)
        assert result.total.timing_pct is None
        assert result.assets[0].missing_days == 1

    def test_yearly_buckets(self) -> None:
        """按年度分桶。"""
        days = [
            AttributionDay(date(2024, 12, 31), 1.0, {}),
            AttributionDay(date(2025, 1, 2), 2.0, {}),
        ]
        result = compute_attribution(days, {})
        assert [(b.label, b.portfolio_pct) for b in result.yearly] == [("2024", 1.0), ("2025", 2.0)]


class TestAttributionService:
    """读取路径编排。"""

    def test_service_builds_response(self) -> None:
        """成功回测返回归因；非成功回测抛错；不存在返回 None。"""
        svc = BacktestService(db=mock.MagicMock())
        svc._backtest_repo = mock.MagicMock()
        svc._backtest_repo.find_by_id.return_value = SimpleNamespace(
            status="success",
            params={"_execution_model": "t_plus_1_close", "_benchmark_index_code": "000300"},
        )
        svc._backtest_repo.find_daily_results.return_value = [
            SimpleNamespace(
                trade_date=_D[0],
                portfolio_return=0.5,
                executed_positions={"A": 0.5},
                positions={"A": 0.5},
                benchmark_return=0.2,
            )
        ]
        svc._backtest_repo.find_held_index_returns.return_value = [(_D[0], "A", 1.0)]
        result = svc.get_attribution("bt")
        assert result is not None and result.approximate is False
        assert result.total.residual_pct == pytest.approx(0.0)
        assert result.benchmark_label == "000300"
        svc._backtest_repo.find_by_id.return_value = SimpleNamespace(status="running", params={})
        with pytest.raises(ValueError):
            svc.get_attribution("bt")
        svc._backtest_repo.find_by_id.return_value = None
        assert svc.get_attribution("bt") is None

    def test_pruned_backtest_raises_instead_of_residual(self) -> None:
        """逐指数明细已瘦身：抛 IndexDetailPrunedError，不把缺失明细算成残差。"""
        svc = BacktestService(db=mock.MagicMock())
        svc._backtest_repo = mock.MagicMock()
        svc._backtest_repo.find_by_id.return_value = SimpleNamespace(
            status="success",
            params={},
            index_detail_pruned_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
        )
        with pytest.raises(IndexDetailPrunedError, match="2026-11-06"):
            svc.get_attribution("bt")
        svc._backtest_repo.find_held_index_returns.assert_not_called()
        with pytest.raises(IndexDetailPrunedError):
            svc.get_index_results("bt")
        svc._backtest_repo.find_index_results.assert_not_called()


class TestBenchmarkMix:
    """混合基准。"""

    def test_mix_is_weighted_buy_hold(self) -> None:
        """混合基准 = Σ 权重 × 各指数买入持有收益（现金 0）。"""
        bars = {
            ("X", _D[0]): SimpleNamespace(close_price=100.0),
            ("X", _D[1]): SimpleNamespace(close_price=110.0),
            ("Y", _D[0]): SimpleNamespace(close_price=50.0),
            ("Y", _D[1]): SimpleNamespace(close_price=45.0),
        }
        mix = compute_mix_benchmark(bars, [("X", 0.6), ("Y", 0.2)], _D[:2])
        x = compute_buy_hold_benchmark(bars, "X", _D[:2])
        y = compute_buy_hold_benchmark(bars, "Y", _D[:2])
        assert mix[0] == pytest.approx(0.6 * x[0] + 0.2 * y[0])
        assert mix[0] == pytest.approx(0.6 * 10 + 0.2 * -10)

    def test_request_validation(self) -> None:
        """重复指数、权重超过 1 被拒绝。"""
        base = {"strategy_id": "s", "start_date": _D[0], "end_date": _D[-1]}
        with pytest.raises(ValidationError):
            BacktestCreateRequest(
                **base,
                benchmark_mix=[
                    {"index_code": "X", "weight": 0.5},
                    {"index_code": "X", "weight": 0.1},
                ],
            )
        with pytest.raises(ValidationError):
            BacktestCreateRequest(
                **base,
                benchmark_mix=[
                    {"index_code": "X", "weight": 0.7},
                    {"index_code": "Y", "weight": 0.4},
                ],
            )
