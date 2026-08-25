"""领域层下沉测试（C6 / 7.5 收敛）。

覆盖：
- 调仓调度器（纯领域实现，交易日历协议注入）
- 换手率 / 组合收益（纯函数）
- 回测账户累积器
- universe 解析（兼容 etf_code 历史字段）
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from quant_etf_api.domain.portfolio.accounting import BacktestDayAccumulator
from quant_etf_api.domain.portfolio.returns import (
    compute_allocation_return,
    get_index_return,
)
from quant_etf_api.domain.portfolio.turnover import compute_turnover
from quant_etf_api.domain.portfolio.universe import (
    build_universe_items,
    filter_universe_rows,
)
from quant_etf_api.domain.strategies.models import UniverseAsset
from quant_etf_api.domain.strategies.rebalance import DefaultRebalanceScheduler
from quant_etf_api.engine.config import RebalanceConfig


class _WeekdayCalendar:
    """仅周末休市的交易日历替身。"""

    def is_trading_day(self, day: date) -> bool:
        """按周末判断。"""
        return day.weekday() < 5


class TestRebalanceScheduler:
    """调仓调度器领域化测试。"""

    def test_daily_always_rebalance(self) -> None:
        """daily 频率永远调仓。"""
        scheduler = DefaultRebalanceScheduler(_WeekdayCalendar())
        assert scheduler.should_rebalance(RebalanceConfig(frequency="daily"), date(2025, 1, 15))

    def test_weekly_target_day(self) -> None:
        """weekly 命中目标 weekday 时调仓。"""
        scheduler = DefaultRebalanceScheduler(_WeekdayCalendar())
        # 2025-01-17 是周五
        cfg = RebalanceConfig(frequency="weekly", day_of_week=4)
        assert scheduler.should_rebalance(cfg, date(2025, 1, 17))
        # 2025-01-15 是周三，非目标日
        assert not scheduler.should_rebalance(cfg, date(2025, 1, 15))

    def test_monthly_target_day(self) -> None:
        """monthly 命中目标日时调仓。"""
        scheduler = DefaultRebalanceScheduler(_WeekdayCalendar())
        cfg = RebalanceConfig(frequency="monthly", day_of_month=15)
        assert scheduler.should_rebalance(cfg, date(2025, 1, 15))
        assert not scheduler.should_rebalance(cfg, date(2025, 1, 16))


class TestTurnoverAndReturns:
    """换手率与收益纯函数测试。"""

    def test_compute_turnover(self) -> None:
        """换手率 = 仓位变动绝对值之和 / 2。"""
        prev = {"a": 0.5, "b": 0.5}
        curr = {"a": 0.8, "b": 0.2}
        assert compute_turnover(prev, curr) == pytest.approx(0.3)

    def test_get_index_return(self) -> None:
        """T+1 收益率按收盘价计算。"""
        bars = {
            ("000300", date(2025, 1, 15)): SimpleNamespace(close_price=100.0),
            ("000300", date(2025, 1, 16)): SimpleNamespace(close_price=103.0),
        }
        assert get_index_return("000300", date(2025, 1, 15), date(2025, 1, 16), bars) == 3.0

    def test_compute_allocation_return(self) -> None:
        """组合收益按权重加权。"""
        bars = {
            ("a", date(2025, 1, 15)): SimpleNamespace(close_price=100.0),
            ("a", date(2025, 1, 16)): SimpleNamespace(close_price=110.0),
            ("b", date(2025, 1, 15)): SimpleNamespace(close_price=100.0),
            ("b", date(2025, 1, 16)): SimpleNamespace(close_price=100.0),
        }
        ret = compute_allocation_return(
            {"a": 0.5, "b": 0.5}, date(2025, 1, 15), date(2025, 1, 16), bars
        )
        assert ret == 5.0


class TestBacktestDayAccumulator:
    """账户累积器测试。"""

    def test_accumulate_cumulative_and_drawdown(self) -> None:
        """累计净值、峰值与回撤应正确跟踪。"""
        acc = BacktestDayAccumulator()
        acc.apply_day(10.0, True)
        acc.apply_day(-5.0, True)
        assert acc.cumulative_return_pct == pytest.approx(4.5)
        assert acc.drawdown_pct == pytest.approx(-5.0)
        assert acc.active_days == 2
        assert acc.daily_returns == [10.0, -5.0]
        assert acc.active_returns == [10.0, -5.0]

    def test_inactive_day_not_counted(self) -> None:
        """无持仓日计入 daily_returns 但不计入 active。"""
        acc = BacktestDayAccumulator()
        acc.apply_day(1.0, False)
        assert acc.active_days == 0
        assert acc.active_returns == []
        assert acc.daily_returns == [1.0]


class TestUniverseDomain:
    """universe 解析与领域模型测试。"""

    def test_build_universe_items_dual_keys(self) -> None:
        """universe 项应同时携带 etf_code（引擎兼容）与 index_code（领域语义）。"""
        rows = [SimpleNamespace(index_code="000300", name_cn="沪深300")]
        items = build_universe_items(rows)
        assert items[0]["index_code"] == "000300"
        assert items[0]["etf_code"] == "000300"

    def test_build_universe_items_filter(self) -> None:
        """按 index_codes 过滤。"""
        rows = [
            SimpleNamespace(index_code="000300", name_cn="沪深300"),
            SimpleNamespace(index_code="000905", name_cn="中证500"),
        ]
        items = build_universe_items(rows, ["000905"])
        assert [i["index_code"] for i in items] == ["000905"]

    def test_filter_universe_rows_subset(self) -> None:
        """subset 模式过滤指数行。"""
        rows = [
            SimpleNamespace(index_code="000300", name_cn="沪深300"),
            SimpleNamespace(index_code="000905", name_cn="中证500"),
        ]
        filtered = filter_universe_rows(rows, {"mode": "subset", "index_codes": ["000905"]})
        assert [r.index_code for r in filtered] == ["000905"]

    def test_universe_asset_to_engine_dict(self) -> None:
        """UniverseAsset 领域模型转换为引擎兼容字典。"""
        asset = UniverseAsset(asset_code="000300", name_cn="沪深300")
        d = asset.to_engine_dict()
        assert d["etf_code"] == "000300"
        assert d["index_code"] == "000300"
