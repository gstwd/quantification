"""月线因子聚合缺数据场景单元测试（B14）。

- 覆盖 OHLC 四价任一缺失（None/NaN）时日线被跳过，0 不进入月线极值
- 覆盖整月数据不完整时该月被跳过
- 覆盖边界日缺失时月线开/收盘取首个/末个完整交易日
- 覆盖三个月线因子的回归冒烟
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.monthly import (
    MonthlyMAComputer,
    MonthlyReturnComputer,
    MonthlyStreakComputer,
    _aggregate_monthly_bars,
)


@dataclass
class MockBar:
    """模拟 IndexDailyBarModel 行，仅保留 OHLC 四价字段。"""

    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float | None


def _make_bars(
    index_code: str, rows: dict[date, tuple[float | None, float | None, float | None, float | None]]
) -> dict[tuple[str, date], MockBar]:
    """构造符合 FactorContext.index_bars 格式的日线映射。

    Args:
        index_code: 指数代码。
        rows: 日期 → (open, high, low, close) 映射，字段可为 None。

    Returns:
        (code, date) → MockBar 的 dict。
    """
    return {(index_code, dt): MockBar(*ohlc) for dt, ohlc in rows.items()}


class TestAggregateMonthlyBarsMissing:
    """月线聚合缺数据场景测试。"""

    def test_complete_ohlc_aggregation(self) -> None:
        """OHLC 完整时正常聚合：开/收取首末交易日，极值取最大/最小。"""
        trade_date = date(2025, 3, 19)
        rows = {
            date(2025, 1, 6): (100.0, 105.0, 99.0, 103.0),
            date(2025, 1, 20): (103.0, 108.0, 102.0, 107.0),
            date(2025, 2, 5): (107.0, 109.0, 101.0, 102.0),
            date(2025, 2, 19): (102.0, 104.0, 98.0, 100.0),
            date(2025, 3, 5): (100.0, 106.0, 99.0, 105.0),
            date(2025, 3, 19): (105.0, 111.0, 104.0, 110.0),
        }
        ctx = FactorContext(index_bars=_make_bars("000300", rows))

        bars = _aggregate_monthly_bars("000300", trade_date, ctx, lookback_months=3)

        assert [b.year_month for b in bars] == ["2025-01", "2025-02", "2025-03"]
        assert bars[0].open == 100.0
        assert bars[0].close == 107.0
        assert bars[0].high == 108.0
        assert bars[0].low == 99.0
        assert bars[1].open == 107.0
        assert bars[1].close == 100.0
        assert bars[1].high == 109.0
        assert bars[1].low == 98.0

    def test_missing_high_low_day_skipped(self) -> None:
        """某日 high/low 为 None 时整日跳过，0 不进入月线极值。"""
        trade_date = date(2025, 2, 19)
        rows = {
            date(2025, 1, 6): (100.0, 105.0, 99.0, 103.0),
            date(2025, 2, 5): (107.0, 109.0, 101.0, 102.0),
            date(2025, 2, 12): (102.0, None, None, 101.0),  # 高低价缺失
            date(2025, 2, 19): (101.0, 104.0, 98.0, 100.0),
        }
        ctx = FactorContext(index_bars=_make_bars("000300", rows))

        bars = _aggregate_monthly_bars("000300", trade_date, ctx, lookback_months=2)
        feb = [b for b in bars if b.year_month == "2025-02"][0]

        assert feb.high == 109.0  # 不含被跳过日，不受 0 影响
        assert feb.low == 98.0
        assert feb.high > 0 and feb.low > 0
        assert feb.open == 107.0
        assert feb.close == 100.0

    def test_nan_price_treated_as_missing(self) -> None:
        """NaN 与 None 同语义：high 为 NaN 的日线被跳过。"""
        trade_date = date(2025, 1, 20)
        rows = {
            date(2025, 1, 6): (100.0, 105.0, 99.0, 103.0),
            date(2025, 1, 20): (103.0, math.nan, 102.0, 107.0),
        }
        ctx = FactorContext(index_bars=_make_bars("000300", rows))

        bars = _aggregate_monthly_bars("000300", trade_date, ctx, lookback_months=1)

        assert bars[0].high == 105.0  # 仅来自完整日
        assert bars[0].close == 103.0  # 末个完整交易日收盘

    def test_month_all_incomplete_skipped(self) -> None:
        """整月 OHLC 不完整时该月从月线序列中跳过。"""
        trade_date = date(2025, 3, 5)
        rows = {
            date(2025, 1, 6): (100.0, 105.0, 99.0, 103.0),
            date(2025, 2, 5): (107.0, None, None, 102.0),
            date(2025, 2, 19): (102.0, None, None, 100.0),
            date(2025, 3, 5): (100.0, 106.0, 99.0, 105.0),
        }
        ctx = FactorContext(index_bars=_make_bars("000300", rows))

        bars = _aggregate_monthly_bars("000300", trade_date, ctx, lookback_months=3)

        assert [b.year_month for b in bars] == ["2025-01", "2025-03"]

    def test_boundary_day_incomplete_uses_next_complete_day(self) -> None:
        """月初首个交易日高低价缺失时，月开盘取首个完整交易日。"""
        trade_date = date(2025, 1, 20)
        rows = {
            date(2025, 1, 6): (100.0, None, None, 103.0),  # 月初首日高低价缺失
            date(2025, 1, 7): (101.0, 106.0, 100.0, 105.0),
            date(2025, 1, 20): (105.0, 108.0, 104.0, 107.0),
        }
        ctx = FactorContext(index_bars=_make_bars("000300", rows))

        bars = _aggregate_monthly_bars("000300", trade_date, ctx, lookback_months=1)

        assert bars[0].open == 101.0
        assert bars[0].close == 107.0
        assert bars[0].high == 108.0
        assert bars[0].low == 100.0


class TestMonthlyFactorsMissingRegression:
    """三个月线因子在缺 high/low 场景下的回归冒烟。"""

    def _build_ctx(self) -> FactorContext:
        """构造 5 个月完整数据、中间夹杂一日高低价缺失的上下文。"""
        rows: dict[date, tuple[float | None, float | None, float | None, float | None]] = {}
        months = [
            ("2025-01", (6, 20)),
            ("2025-02", (5, 19)),
            ("2025-03", (5, 19)),
            ("2025-04", (7, 18)),
            ("2025-05", (8, 19)),
        ]
        for idx, (ym, (d1, d2)) in enumerate(months):
            base = 100.0 + idx * 5.0
            rows[date.fromisoformat(f"{ym}-{d1:02d}")] = (base, base + 3.0, base - 2.0, base + 1.0)
            if idx == 2:
                # 3 月中间一日高低价缺失
                rows[date.fromisoformat(f"{ym}-12")] = (base + 1.0, None, None, base + 1.5)
            rows[date.fromisoformat(f"{ym}-{d2:02d}")] = (
                base + 1.0,
                base + 4.0,
                base - 1.0,
                base + 2.0,
            )
        return FactorContext(index_bars=_make_bars("000300", rows))

    def test_monthly_ma_computes(self) -> None:
        """月线均线在缺 high/low 时仍可计算且为正。"""
        ctx = self._build_ctx()
        result = MonthlyMAComputer(period=3).compute("000300", date(2025, 5, 19), ctx)
        assert result.numeric is not None
        assert result.numeric > 0

    def test_monthly_return_computes(self) -> None:
        """月线动量在缺 high/low 时仍可计算。"""
        ctx = self._build_ctx()
        result = MonthlyReturnComputer(period=2).compute("000300", date(2025, 5, 19), ctx)
        assert result.numeric is not None

    def test_monthly_streak_computes(self) -> None:
        """连续收阳月数在缺 high/low 时仍可计算。"""
        ctx = self._build_ctx()
        result = MonthlyStreakComputer().compute("000300", date(2025, 5, 19), ctx)
        assert result.numeric is not None
