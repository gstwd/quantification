"""walk-forward 验证窗口切分单元测试。"""

from __future__ import annotations

from datetime import date

import pytest

from quant_etf_api.domain.research.walk_forward import compute_folds


def _dates(count: int) -> list[date]:
    """生成从 2024-01-01 起连续递增的日期序列。"""
    base = date(2024, 1, 1)
    return [base.replace(day=base.day + i) for i in range(count)]


class TestComputeFolds:
    """compute_folds 窗口切分。"""

    def test_splits_into_k_contiguous_windows(self) -> None:
        """12 个交易日切 4 折，每折 3 天且区间连续覆盖。"""
        dates = _dates(12)
        folds = compute_folds(dates, 4)
        assert len(folds) == 4
        assert folds[0] == (dates[0], dates[2])
        assert folds[1] == (dates[3], dates[5])
        assert folds[2] == (dates[6], dates[8])
        assert folds[3] == (dates[9], dates[11])

    def test_last_fold_absorbs_remainder(self) -> None:
        """余数并入最后一折。"""
        dates = _dates(10)
        folds = compute_folds(dates, 4)
        assert len(folds) == 4
        assert folds[0] == (dates[0], dates[1])
        assert folds[3] == (dates[6], dates[9])

    def test_k_equals_one_returns_whole_range(self) -> None:
        """k=1 时返回整个区间。"""
        dates = _dates(5)
        assert compute_folds(dates, 1) == [(dates[0], dates[-1])]

    def test_insufficient_dates_raises(self) -> None:
        """交易日少于 k 时报错。"""
        with pytest.raises(ValueError, match="交易日不足"):
            compute_folds(_dates(3), 4)

    def test_invalid_k_raises(self) -> None:
        """k < 1 报错。"""
        with pytest.raises(ValueError, match="必须 >= 1"):
            compute_folds(_dates(5), 0)
