"""通用数值工具单元测试。"""

from __future__ import annotations

from quant_etf_api.domain.common.numeric import mean_or_none, median_or_none


class TestMeanMedian:
    """均值 / 中位数的空值语义。"""

    def test_empty_returns_none(self) -> None:
        """空序列返回 None。"""
        assert mean_or_none([]) is None
        assert median_or_none([]) is None

    def test_mean(self) -> None:
        """算术平均。"""
        assert mean_or_none([1.0, 2.0, 6.0]) == 3.0

    def test_median_odd_and_even(self) -> None:
        """奇数长度取中间值，偶数长度取中间两数均值。"""
        assert median_or_none([3.0, 1.0, 2.0]) == 2.0
        assert median_or_none([4.0, 1.0, 2.0, 3.0]) == 2.5
