"""行业轮动收敛后的领域规则单元测试（月末日/参数指纹）。

旧独立回测模拟（simulate_rotation_backtest）已随标准回测收敛删除，
收益口径正确性由标准回测集成测试覆盖。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.domain.industry.constants import (
    DEFAULT_DIFFUSION_LOOKBACK,
    DEFAULT_LOOKBACK_MOM,
    DEFAULT_LOOKBACK_RATIO,
    DEFAULT_SMOOTH_WINDOW,
    canonical_industry_params,
    industry_params_hash,
)
from quant_etf_api.domain.industry.rebalance import month_end_dates


def test_month_end_dates() -> None:
    """month_end_dates 提取每月最后一个交易日。"""
    dates = [
        date(2024, 1, 2),
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 1),
        date(2024, 3, 4),
    ]
    assert month_end_dates(dates) == [
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 4),
    ]


def test_canonical_industry_params_defaults() -> None:
    """默认参数规范化后含研报复刻口径与基准剔除清单。"""
    params = canonical_industry_params()
    assert params["lookback_ratio"] == DEFAULT_LOOKBACK_RATIO == 220
    assert params["lookback_mom"] == DEFAULT_LOOKBACK_MOM == 60
    assert params["smooth_window"] == DEFAULT_SMOOTH_WINDOW == 20
    assert params["diffusion_lookback"] == DEFAULT_DIFFUSION_LOOKBACK == 220
    assert params["benchmark_exclude"] == ["801230"]


def test_industry_params_hash_stable_and_distinct() -> None:
    """参数指纹稳定且不同参数组合生成不同指纹。"""
    h1 = industry_params_hash()
    h2 = industry_params_hash()
    assert h1 == h2
    assert len(h1) == 64
    h3 = industry_params_hash(diffusion_lookback=200)
    assert h3 != h1
    # 显式传入与默认相同的基准剔除清单指纹一致
    h4 = industry_params_hash(benchmark_exclude=["801230"])
    assert h4 == h1
