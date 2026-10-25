"""Deflated Sharpe 门槛校准测试：批量 DSR 与单条公式一致、零假设与功效模拟的基本性质。"""

from __future__ import annotations

import random
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from quant_etf_api.domain.research.dsr_calibration import (
    circular_block_indices,
    deflated_sharpe_batch,
    null_pass_rates,
    power_curve,
    series_profile,
    synthetic_sparse_excess,
)
from quant_etf_api.domain.research.robustness import deflated_sharpe_ratio
from quant_etf_api.services.dsr_calibration_service import DsrCalibrationService, render_markdown


def _sparse(seed: int, n: int = 600, active: float = 0.1, mean: float = 0.0) -> list[float]:
    """构造稀疏超额序列：多数为 0，差异日为带均值的随机值。"""
    rng = random.Random(seed)
    return [(mean + rng.gauss(0.0, 0.01)) if rng.random() < active else 0.0 for _ in range(n)]


class TestDeflatedSharpeBatch:
    """批量 DSR 必须与 robustness.deflated_sharpe_ratio 逐项一致。"""

    @pytest.mark.parametrize("n_trials", [1, 2, 37, 500])
    def test_matches_scalar_formula(self, n_trials: int) -> None:
        """不同 N 下批量结果与单条公式相同。"""
        rows = [_sparse(seed, mean=0.002 * (seed % 3)) for seed in range(6)]
        batch = deflated_sharpe_batch(np.asarray(rows), n_trials)
        for row, value in zip(rows, batch):
            expected = deflated_sharpe_ratio(row, n_trials).deflated_sharpe
            assert value == pytest.approx(expected, abs=1e-9)

    def test_zero_volatility_row_is_zero(self) -> None:
        """零波动行与短序列返回 0。"""
        assert deflated_sharpe_batch(np.zeros((2, 50)), 5).tolist() == [0.0, 0.0]
        assert deflated_sharpe_batch(np.ones((1, 2)), 5).tolist() == [0.0]

    def test_rejects_invalid_trials(self) -> None:
        """N < 1 抛错。"""
        with pytest.raises(ValueError):
            deflated_sharpe_batch(np.zeros((1, 10)), 0)


class TestSimulation:
    """零假设与功效模拟。"""

    def test_block_indices_shape_and_range(self) -> None:
        """下标矩阵形状正确且循环取值不越界。"""
        idx = circular_block_indices(101, 20, 7, np.random.default_rng(1))
        assert idx.shape == (7, 101)
        assert idx.min() >= 0 and idx.max() < 101

    def test_null_is_reproducible(self) -> None:
        """固定种子结果可复现。"""
        series = [_sparse(3)]
        a = null_pass_rates(series, 1, n_sims=200, seed=9)
        b = null_pass_rates(series, 1, n_sims=200, seed=9)
        assert a.pass_rates == b.pass_rates
        assert a.mode == "single"

    def test_null_removes_true_mean(self) -> None:
        """零假设样本抹掉真实均值：强正超额序列在零假设下也很少通过。"""
        strong = [_sparse(5, mean=0.01)]
        assert deflated_sharpe_ratio(strong[0], 1).deflated_sharpe > 0.99
        result = null_pass_rates(strong, 1, n_sims=400, seed=1)
        assert result.pass_rates[0.95] < 0.2

    def test_more_trials_lower_false_positive(self) -> None:
        """N 越大，同一零假设样本的通过率越低。"""
        series = [_sparse(7)]
        low = null_pass_rates(series, 1, n_sims=400, seed=2).pass_rates[0.95]
        high = null_pass_rates(series, 200, n_sims=400, seed=2).pass_rates[0.95]
        assert high <= low

    def test_selected_mode_inflates_without_deflation(self) -> None:
        """从多条独立序列挑最优而 N=1 时，假阳性率不低于按 N=条数折减。"""
        group = synthetic_sparse_excess(n_days=600, n_series=10, correlation=0.0, seed=4)
        naive = null_pass_rates(group, 1, n_sims=300, seed=3)
        deflated = null_pass_rates(group, 10, n_sims=300, seed=3)
        assert naive.mode == "selected"
        assert naive.pass_rates[0.95] >= deflated.pass_rates[0.95]

    def test_power_increases_with_injected_sharpe(self) -> None:
        """注入的超额夏普越大，通过率不降。"""
        points = power_curve(_sparse(11), 1, injected_sharpes=(0.0, 1.0, 3.0), n_sims=200)
        rates = [p.pass_rates[0.95] for p in points]
        assert rates == sorted(rates)
        assert rates[-1] > rates[0]

    def test_power_keeps_zero_days(self) -> None:
        """无差异日的序列不产生功效（漂移只加在差异日上）。"""
        points = power_curve([0.0] * 100, 1, injected_sharpes=(1.0,), n_sims=20)
        assert points[0].pass_rates[0.95] == 0.0

    def test_synthetic_shape(self) -> None:
        """合成序列条数、长度与差异日占比符合参数。"""
        rows = synthetic_sparse_excess(n_days=1000, active_ratio=0.1, n_series=3, seed=8)
        assert len(rows) == 3 and all(len(r) == 1000 for r in rows)
        profile = series_profile(rows[0])
        assert 50 <= profile.active_days <= 200
        assert profile.kurtosis > 3

    def test_input_validation(self) -> None:
        """空序列、模拟次数为 0 时抛错。"""
        with pytest.raises(ValueError):
            null_pass_rates([], 1)
        with pytest.raises(ValueError):
            null_pass_rates([[0.1, 0.2, 0.3]], 1, n_sims=0)
        with pytest.raises(ValueError):
            power_curve([], 1)


def _daily(start: date, values: list[float]) -> list[SimpleNamespace]:
    """构造回测逐日结果行（百分比口径，无换手）。"""
    return [
        SimpleNamespace(trade_date=start + timedelta(days=i), portfolio_return=v, turnover=None)
        for i, v in enumerate(values)
    ]


def _service(daily: dict[str, list[SimpleNamespace]]) -> DsrCalibrationService:
    """构造仓库全部替换为 MagicMock 的校准服务。"""
    svc = DsrCalibrationService(MagicMock())
    svc._backtest_repo = MagicMock()
    svc._backtest_repo.find_by_id.side_effect = lambda bid: (
        SimpleNamespace(status="success") if bid in daily else None
    )
    svc._backtest_repo.find_daily_results.side_effect = lambda bid: daily.get(bid, [])
    svc._ledger = MagicMock()
    svc._ledger.trial_breakdown.return_value = {"robustness": 9, "optimization": 1}
    svc._ledger.lineage_scope.return_value = {"lineage_id": "base", "members": ["base"]}
    svc._ledger_repo = MagicMock()
    return svc


class TestDsrCalibrationService:
    """服务层：从会话与稳健性批次抽取超额序列。"""

    def test_report_from_session_and_run(self) -> None:
        """会话与批次都能产出校准结果，报告可渲染。"""
        start = date(2020, 1, 1)
        mid = start + timedelta(days=150)
        base = [x * 100 for x in _sparse(1, n=300, active=0.5)]
        cand = [b + x * 100 for b, x in zip(base, _sparse(2, n=300))]
        var_a = [b + x * 100 for b, x in zip(base, _sparse(3, n=300))]
        var_b = [b + x * 100 for b, x in zip(base, _sparse(4, n=300))]
        daily = {
            "bt_base": _daily(start, base),
            "bt_cand": _daily(start, cand),
            "w0_base": _daily(start, base[:150]),
            "w1_base": _daily(mid, base[150:]),
            "w0_a": _daily(start, var_a[:150]),
            "w1_a": _daily(mid, var_a[150:]),
            "w0_b": _daily(start, var_b[:150]),
            "w1_b": _daily(mid, var_b[150:]),
        }
        svc = _service(daily)
        svc._ledger_repo.find_evaluated_optimizations.return_value = [
            SimpleNamespace(
                optimization_id="opt1",
                strategy_id="base",
                status="rejected",
                baseline_backtest_id="bt_base",
                candidate_backtest_id="bt_cand",
            )
        ]
        svc._ledger_repo.find_successful_robustness_runs.return_value = [
            SimpleNamespace(
                robustness_id="rb1",
                strategy_id="base",
                kind="scan",
                windows=[{"label": "W0"}, {"label": "W1"}],
                variants=[
                    {
                        "kind": "baseline",
                        "label": "baseline",
                        "backtest_ids": {"W0": "w0_base", "W1": "w1_base"},
                    },
                    {"kind": "knob", "label": "a", "backtest_ids": {"W0": "w0_a", "W1": "w1_a"}},
                    {"kind": "knob", "label": "b", "backtest_ids": {"W0": "w0_b", "W1": "w1_b"}},
                    {
                        "kind": "timing_off",
                        "label": "t",
                        "backtest_ids": {"W0": "w0_a", "W1": "w1_a"},
                    },
                ],
            )
        ]
        report = svc.calibrate("base", n_sims=50, power_sims=20)
        assert report["summary"]["n_sessions"] == 1
        assert report["summary"]["n_robustness_runs"] == 1
        session = report["sessions"][0]
        assert session["n_trials"] == 10
        assert session["profile"]["n_observations"] == 300
        run = report["robustness"][0]
        assert run["n_candidates"] == 2  # baseline 与 timing_off 不参与挑选
        assert run["common_days"] == 300
        markdown = render_markdown(report)
        assert "Deflated Sharpe 门槛校准报告" in markdown
        assert "opt1" in markdown and "rb1" in markdown

    def test_skips_missing_backtests(self) -> None:
        """全区间回测缺失的会话与基线缺失的批次被跳过。"""
        svc = _service({})
        svc._ledger_repo.find_evaluated_optimizations.return_value = [
            SimpleNamespace(
                optimization_id="opt1",
                strategy_id="base",
                status="rejected",
                baseline_backtest_id=None,
                candidate_backtest_id="x",
            )
        ]
        svc._ledger_repo.find_successful_robustness_runs.return_value = [
            SimpleNamespace(
                robustness_id="r",
                strategy_id="base",
                kind="scan",
                windows=[],
                variants=[{"kind": "knob", "label": "a", "backtest_ids": {}}],
            )
        ]
        report = svc.calibrate(None, n_sims=20, power_sims=10)
        assert report["sessions"] == [] and report["robustness"] == []
        assert report["summary"]["session_fpr_lineage_n"]["0.95"]["median"] is None
        assert len(report["synthetic"]) == 4
