"""行业轮动领域规则单元测试（月末日）。

旧独立回测模拟（simulate_rotation_backtest）已随标准回测收敛删除，
收益口径正确性由标准回测集成测试覆盖。
"""

from __future__ import annotations

from datetime import date

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

