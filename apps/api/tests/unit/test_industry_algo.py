"""申万行业纯算法测试：RRG / 象限 / 扩散 / 信号选择语义。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_etf_api.domain.industry.factor_algo import (
    classify_quadrant,
    compute_rs_ratio,
    compute_rrg,
    diffusion_count_ratio,
    equal_weight_benchmark,
    select_by_diffusion,
    select_by_quadrant,
    select_diffusion_with_rrg,
)


def _make_panel(days: int, cols: list[str], base: float = 100.0) -> pd.DataFrame:
    """构造确定性价格面板（每列线性上行）。"""
    dates = pd.bdate_range("2020-01-01", periods=days)
    data = {col: base + (idx + 1) * 0.5 + float(idx) * 0.1 for idx, col in enumerate(cols)}
    return pd.DataFrame(data, index=dates)


def test_rs_ratio_warmup_rows() -> None:
    """RS-Ratio 头部 warm-up 行数应为 lookback + smooth - 1。"""
    price = _make_panel(300, ["801010", "801030"])
    benchmark = equal_weight_benchmark(price)
    result = compute_rs_ratio(price, benchmark, lookback=40, smooth_window=5)
    first_valid = result.notna().any(axis=1).idxmax()
    assert (result.index.get_loc(first_valid)) == 40 + 5 - 1


def test_compute_rrg_returns_both_axis_and_warmup() -> None:
    """compute_rrg 返回 (ratio, momentum)，momentum warm-up 更长。"""
    price = _make_panel(500, ["801010", "801030", "801040"])
    benchmark = equal_weight_benchmark(price)
    ratio, momentum = compute_rrg(
        price, benchmark, lookback_ratio=40, lookback_mom=20, smooth_window=5
    )
    assert ratio.shape == momentum.shape == price.shape
    ratio_first = ratio.notna().any(axis=1).idxmax()
    mom_first = momentum.notna().any(axis=1).idxmax()
    assert momentum.index.get_loc(mom_first) > ratio.index.get_loc(ratio_first)


def test_rrg_gap_strictly_invalidates_affected_window() -> None:
    """任一行业缺日时 RRG 不前填，缺口影响窗口后才允许恢复。"""
    price = _make_panel(90, ["801010", "801030"])
    gap_date = price.index[30]
    price.loc[gap_date, "801030"] = np.nan
    benchmark = equal_weight_benchmark(price)
    ratio, momentum = compute_rrg(
        price,
        benchmark,
        lookback_ratio=20,
        lookback_mom=10,
        smooth_window=3,
    )

    assert benchmark.loc[gap_date] != benchmark.loc[gap_date]
    assert ratio.loc[gap_date, "801010"] != ratio.loc[gap_date, "801010"]
    assert ratio.loc[gap_date + pd.offsets.BDay(3), "801010"] != ratio.loc[
        gap_date + pd.offsets.BDay(3), "801010"
    ]
    assert momentum.loc[gap_date + pd.offsets.BDay(20), "801010"] != momentum.loc[
        gap_date + pd.offsets.BDay(20), "801010"
    ]


def test_quadrant_boundary_does_not_classify() -> None:
    """恰为 100 或 NaN 的值不归入任何象限。"""
    ratio = pd.DataFrame(
        {"a": [100.0], "b": [101.0], "c": [np.nan]},
        index=pd.bdate_range("2020-01-01", periods=1),
    )
    momentum = pd.DataFrame(
        {"a": [101.0], "b": [99.0], "c": [101.0]},
        index=pd.bdate_range("2020-01-01", periods=1),
    )
    quad = classify_quadrant(ratio, momentum)
    assert quad.loc[quad.index[0], "a"] != quad.loc[quad.index[0], "a"]  # NaN
    assert quad.loc[quad.index[0], "b"] == 4.0
    assert quad.loc[quad.index[0], "c"] != quad.loc[quad.index[0], "c"]


def test_diffusion_count_ratio_simple_case() -> None:
    """数量占比扩散：两涨两平 → 0.5，全员无效 → NaN。"""
    dates = pd.bdate_range("2020-01-01", periods=15)
    close = pd.DataFrame(
        {
            "s1": [100.0 + i for i in range(15)],
            "s2": [200.0 + i for i in range(15)],
            "s3": [50.0] * 15,
            "s4": [np.nan] * 15,
        },
        index=dates,
    )
    membership = pd.DataFrame(
        {
            "s1": "801010",
            "s2": "801010",
            "s3": "801010",
            "s4": np.nan,
        },
        index=dates,
        columns=close.columns,
    )
    result = diffusion_count_ratio(close, membership, lookback=5, smooth_window=3)
    last = result.iloc[-1]
    assert abs(float(last.iloc[0]) - 2.0 / 3.0) < 1e-9


def test_select_diffusion_with_rrg_no_backfill() -> None:
    """信号 C：扩散 top_n 后剔除三/四象限，不补足。"""
    dates = pd.bdate_range("2020-01-01", periods=2)
    diffusion = pd.DataFrame(
        {"a": [0.9, 0.9], "b": [0.8, 0.8], "c": [0.7, 0.7], "d": [0.6, 0.6]},
        index=dates,
    )
    ratio = pd.DataFrame(
        {"a": [110.0, 110.0], "b": [90.0, 90.0], "c": [120.0, 120.0], "d": [80.0, 80.0]},
        index=dates,
    )
    momentum = pd.DataFrame(
        {"a": [110.0, 110.0], "b": [110.0, 110.0], "c": [90.0, 90.0], "d": [80.0, 80.0]},
        index=dates,
    )
    mask = select_diffusion_with_rrg(diffusion, ratio, momentum, top_n=3, keep_quadrants=(1, 2))
    row = mask.iloc[0]
    # 扩散前三 a/b/c；c 落在第 3 象限（120,90）被剔除 → 只留 a、b
    assert list(row[row].index) == ["a", "b"]


def test_select_by_diffusion_topn() -> None:
    """信号 B：扩散 top_n。"""
    diffusion = pd.DataFrame(
        {"a": [0.9], "b": [0.5], "c": [0.7]},
        index=pd.bdate_range("2020-01-01", periods=1),
    )
    mask = select_by_diffusion(diffusion, top_n=2)
    assert list(mask.iloc[0][mask.iloc[0]].index) == ["a", "c"]


def test_select_by_quadrant_uses_distance() -> None:
    """信号 A：象限内按距中心距离取最远。"""
    ratio = pd.DataFrame(
        {"far": [130.0], "near": [105.0], "other": [80.0]},
        index=pd.bdate_range("2020-01-01", periods=1),
    )
    momentum = pd.DataFrame(
        {"far": [130.0], "near": [105.0], "other": [110.0]},
        index=pd.bdate_range("2020-01-01", periods=1),
    )
    mask = select_by_quadrant(ratio, momentum, quadrants=(1,), top_n=1)
    assert list(mask.iloc[0][mask.iloc[0]].index) == ["far"]


def test_insufficient_length_raises() -> None:
    """输入不足时显式报错而非静默全 NaN。"""
    price = _make_panel(50, ["801010"])
    benchmark = equal_weight_benchmark(price)
    with pytest.raises(ValueError):
        compute_rs_ratio(price, benchmark, lookback=220, smooth_window=20)
