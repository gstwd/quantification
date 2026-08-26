"""回测指标 NaN/Inf 防御单元测试。

覆盖：
- _sanitize_metric_value：NaN/Inf 转 None，有限值原样返回
- _safe_metric_diff：任一输入 NaN/Inf/None 时返回 None
"""

from __future__ import annotations

import math

from quant_etf_api.services.backtest_service import (
    _safe_metric_diff,
    _sanitize_metric_value,
)


class TestSanitizeMetricValue:
    """指标值清洗。"""

    def test_finite_value_passthrough(self) -> None:
        """有限数值原样返回。"""
        assert _sanitize_metric_value(12.34) == 12.34
        assert _sanitize_metric_value(0.0) == 0.0
        assert _sanitize_metric_value(5) == 5

    def test_nan_converted_to_none(self) -> None:
        """NaN 转为 None。"""
        assert _sanitize_metric_value(float("nan")) is None

    def test_inf_converted_to_none(self) -> None:
        """正负无穷转为 None。"""
        assert _sanitize_metric_value(float("inf")) is None
        assert _sanitize_metric_value(float("-inf")) is None

    def test_none_passthrough(self) -> None:
        """None 保持 None。"""
        assert _sanitize_metric_value(None) is None


class TestSafeMetricDiff:
    """对比指标差值容错。"""

    def test_normal_diff(self) -> None:
        """正常差值计算。"""
        assert _safe_metric_diff(10.0, 3.0) == 7.0
        assert _safe_metric_diff(10.0, 3.0, 4) == 7.0

    def test_none_input_returns_none(self) -> None:
        """任一输入为 None 时返回 None。"""
        assert _safe_metric_diff(None, 3.0) is None
        assert _safe_metric_diff(10.0, None) is None

    def test_nan_input_returns_none(self) -> None:
        """任一输入为 NaN 时返回 None。"""
        assert _safe_metric_diff(float("nan"), 3.0) is None
        assert _safe_metric_diff(10.0, float("nan")) is None

    def test_inf_input_returns_none(self) -> None:
        """任一输入为无穷时返回 None。"""
        assert _safe_metric_diff(float("inf"), 3.0) is None
        assert _safe_metric_diff(10.0, float("-inf")) is None


def test_sanitize_metrics_dict() -> None:
    """指标字典整体清洗后不含 NaN（模拟 _compute_summary_metrics 输出）。"""
    raw = {
        "cumulative_return_pct": float("nan"),
        "sharpe_ratio": math.inf,
        "win_rate_pct": 55.5,
        "benchmark_return_pct": None,
    }
    cleaned = {k: _sanitize_metric_value(v) for k, v in raw.items()}
    assert cleaned == {
        "cumulative_return_pct": None,
        "sharpe_ratio": None,
        "win_rate_pct": 55.5,
        "benchmark_return_pct": None,
    }
