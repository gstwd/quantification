"""内置因子计算器单元测试。

- 构造 mock FactorContext（含模拟的 index_bars dict）
- 测试每个 FactorComputer 的核心计算逻辑
- 覆盖：正常值、数据不足返回 None、公式验证、单调性
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.breadth import BreadthMA20Computer
from quant_etf_api.factors.builtins.macro import PMIMomentumComputer
from quant_etf_api.factors.builtins.momentum import (
    Return20dComputer,
    Return5dComputer,
    Return60dComputer,
    Sharpe60dComputer,
    _calc_nd_return,
)
from quant_etf_api.factors.builtins.technical import (
    DrawdownCurrentComputer,
    MADeviationComputer,
)
from quant_etf_api.factors.builtins.volatility import Volatility20dComputer
from quant_etf_api.factors.builtins.volume import AmountRatio20dComputer, VolumeRatio20dComputer
from quant_etf_api.factors.registry import build_default_factor_registry


# ─── 测试辅助：轻量 mock 数据行 ─────────────────────────────────────────────────


@dataclass
class MockBar:
    """模拟 IndexDailyBarModel 行，仅保留计算所需字段。"""

    volume: float | None = None
    close_price: float | None = None
    change_pct: float | None = None
    turnover: float | None = None


def _build_index_bars(
    index_code: str,
    trade_date: date,
    n_days: int,
    base_volume: float = 1000.0,
    base_close: float = 100.0,
    daily_return: float = 0.001,
) -> dict:
    """构造指定指数的 n_days 天历史 K 线数据（含 trade_date 当日）。

    从 trade_date 向前推 n_days-1 天，收盘价按 daily_return 递增。

    Args:
        index_code: 指数代码。
        trade_date: 最新交易日。
        n_days: 生成的历史天数（含 trade_date 当日）。
        base_volume: 最早一日的成交量。
        base_close: 最早一日的收盘价。
        daily_return: 每日收益率，用于模拟价格序列。

    Returns:
        符合 FactorContext.index_bars 格式的 dict。
    """
    bars = {}
    close = base_close
    for i in range(n_days - 1, -1, -1):
        dt = trade_date - timedelta(days=i)
        bars[(index_code, dt)] = MockBar(
            volume=base_volume * (1 + (n_days - 1 - i) * 0.01),
            close_price=round(close, 6),
            change_pct=round(daily_return * 100, 4),
        )
        close = close * (1 + daily_return)
    return bars


# ─── VolumeRatio20dComputer ───────────────────────────────────────────────────────


class TestVolumeRatio20dComputer:
    _computer = VolumeRatio20dComputer()

    def test_spec_factor_id(self) -> None:
        assert self._computer.spec.factor_id == "volume_ratio_20d"

    def test_spec_category(self) -> None:
        assert self._computer.spec.category == "volume"

    def test_normal_compute(self) -> None:
        """正常场景：有足够历史数据，量比应为正数。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=25)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0

    def test_no_data_returns_default(self) -> None:
        """无任何数据时应返回 None（区分无数据与量比恰好为 1）。"""
        ctx = FactorContext()
        result = self._computer.compute("510300", date(2024, 6, 1), ctx)
        assert result.numeric is None

    def test_missing_today_bar(self) -> None:
        """当日无 K 线时返回 None。"""
        trade_date = date(2024, 6, 1)
        # 只有前一天的数据，没有当日
        bars = _build_index_bars("510300", trade_date - timedelta(days=1), n_days=20)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None

    def test_payload_contains_lookback(self) -> None:
        """payload 中应包含 lookback_days 字段。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=25)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.payload.get("lookback_days") == 20


# ─── _calc_nd_return 通用函数 ─────────────────────────────────────────────────────


class TestCalcNdReturn:
    def test_returns_none_when_no_today_bar(self) -> None:
        ctx = FactorContext()
        assert _calc_nd_return("510300", date(2024, 6, 1), ctx, n=5) is None

    def test_returns_none_when_insufficient_history(self) -> None:
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=4)  # 只有3条历史
        ctx = FactorContext(index_bars=bars)
        assert _calc_nd_return("510300", trade_date, ctx, n=5) is None

    def test_positive_return_on_rising_prices(self) -> None:
        """价格单调上涨时，收益率应为正。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=10, daily_return=0.01)
        ctx = FactorContext(index_bars=bars)
        result = _calc_nd_return("510300", trade_date, ctx, n=5)
        assert result is not None
        assert result > 0

    def test_negative_return_on_falling_prices(self) -> None:
        """价格单调下跌时，收益率应为负。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=10, daily_return=-0.01)
        ctx = FactorContext(index_bars=bars)
        result = _calc_nd_return("510300", trade_date, ctx, n=5)
        assert result is not None
        assert result < 0


# ─── Return5dComputer ─────────────────────────────────────────────────────────────


class TestReturn5dComputer:
    _computer = Return5dComputer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "return_5d"
        assert self._computer.spec.category == "momentum"

    def test_positive_return(self) -> None:
        """价格单调上涨时，5日收益率应为正值。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=10, daily_return=0.01)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 4.0  # 每日 1%，5日约 5%

    def test_none_when_insufficient_data(self) -> None:
        """历史数据不足5条时应返回 None。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=3)  # 只有2条历史
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None


# ─── Return20dComputer ────────────────────────────────────────────────────────────


class TestReturn20dComputer:
    _computer = Return20dComputer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "return_20d"

    def test_requires_20_history_bars(self) -> None:
        """少于20条历史数据时应返回 None。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=15)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None

    def test_returns_value_with_sufficient_data(self) -> None:
        """满足21条数据时应返回非 None 值。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=25, daily_return=0.002)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0

    def test_monotone_property(self) -> None:
        """n=5 的收益率应小于 n=20 的收益率（同等每日涨幅下，时间窗口越长收益越大）。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=25, daily_return=0.01)
        ctx = FactorContext(index_bars=bars)
        r5 = Return5dComputer().compute("510300", trade_date, ctx).numeric
        r20 = Return20dComputer().compute("510300", trade_date, ctx).numeric
        assert r5 is not None and r20 is not None
        assert r20 > r5


# ─── Return60dComputer ────────────────────────────────────────────────────────────


class TestReturn60dComputer:
    _computer = Return60dComputer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "return_60d"

    def test_requires_60_history_bars(self) -> None:
        """少于60条历史数据时应返回 None。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=30)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None

    def test_returns_value_with_sufficient_data(self) -> None:
        """满足61条数据时应返回非 None 值。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=65, daily_return=0.002)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0


# ─── Volatility20dComputer ────────────────────────────────────────────────────────


class TestVolatility20dComputer:
    _computer = Volatility20dComputer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "volatility_20d"
        assert self._computer.spec.category == "volatility"

    def test_insufficient_data(self) -> None:
        """少于21个收盘价时应返回 None。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=10)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None
        assert result.payload.get("required") == 20

    def test_zero_return_sequence(self) -> None:
        """收益率全为 0 时（价格不变），波动率应为 0。"""
        trade_date = date(2024, 6, 1)
        bars = {
            ("510300", trade_date - timedelta(days=i)): MockBar(close_price=100.0)
            for i in range(25)
        }
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric == 0.0

    def test_high_vol_greater_than_low_vol(self) -> None:
        """高波动序列（大幅交替涨跌）的年化波动率应大于低波动序列（小幅交替涨跌）。"""
        trade_date = date(2024, 6, 1)

        # 低波动：交替 +0.1%/-0.1%，25条
        closes_low = [100.0]
        for i in range(24):
            closes_low.append(closes_low[-1] * (1.001 if i % 2 == 0 else 0.999))
        bars_low = {
            ("510300", trade_date - timedelta(days=24 - i)): MockBar(close_price=closes_low[i])
            for i in range(25)
        }
        ctx_low = FactorContext(index_bars=bars_low)
        result_low = self._computer.compute("510300", trade_date, ctx_low)

        # 高波动：交替 +3%/-3%，25条
        closes_high = [100.0]
        for i in range(24):
            closes_high.append(closes_high[-1] * (1.03 if i % 2 == 0 else 0.97))
        bars_high = {
            ("510300", trade_date - timedelta(days=24 - i)): MockBar(close_price=closes_high[i])
            for i in range(25)
        }
        ctx_high = FactorContext(index_bars=bars_high)
        result_high = self._computer.compute("510300", trade_date, ctx_high)

        assert result_low.numeric is not None
        assert result_high.numeric is not None
        assert result_high.numeric > result_low.numeric

    def test_formula_verification(self) -> None:
        """手动验证公式：std(ddof=1) × sqrt(252) × 100。"""
        trade_date = date(2024, 6, 1)
        # 构造已知收益率：交替 +1%/-1%，21个收盘价
        closes = [100.0]
        for i in range(20):
            closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
        bars = {
            ("510300", trade_date - timedelta(days=20 - i)): MockBar(close_price=closes[i])
            for i in range(21)
        }
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)

        # 手动计算期望值
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, 21)]
        mean = sum(returns) / len(returns)
        std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
        expected = round(std * math.sqrt(252) * 100, 4)

        assert result.numeric == pytest.approx(expected, rel=1e-4)

    def test_result_is_positive(self) -> None:
        """有真实波动的价格序列，年化波动率应为正数。"""
        trade_date = date(2024, 6, 1)
        # 使用交替涨跌产生非零方差
        closes = [100.0]
        for i in range(24):
            closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
        bars = {
            ("510300", trade_date - timedelta(days=24 - i)): MockBar(close_price=closes[i])
            for i in range(25)
        }
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0

    def test_payload_contains_sample_count(self) -> None:
        """payload 中应包含 sample_count 字段。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=25)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert "sample_count" in result.payload


# ─── FactorRegistry ──────────────────────────────────────────────────────────────


class TestFactorRegistry:
    def test_default_registry_has_all_factors(self) -> None:
        """默认注册表应包含全部内置因子。"""
        registry = build_default_factor_registry()
        assert len(registry.all()) == 38

    def test_default_registry_factor_ids(self) -> None:
        """默认注册表的 factor_id 集合应包含核心因子。"""
        registry = build_default_factor_registry()
        ids = {c.spec.factor_id for c in registry.all()}
        # 核心因子应全部存在
        assert ids >= {
            "volume_ratio_17d",
            "volume_ratio_20d",
            "amount_ratio_20d",
            "return_5d",
            "return_17d",
            "return_20d",
            "return_60d",
            "return_120d",
            "sharpe_60d",
            "volatility_17d",
            "volatility_20d",
            "pe_percentile",
            "pb_percentile",
            "ma_5d",
            "ma_10d",
            "ma_17d",
            "ma_20d",
            "ma_60d",
            "ma60d_deviation",
            "drawdown_current",
            "donchian_17d_high",
            "donchian_17d_low",
            "donchian_20d_high",
            "donchian_20d_low",
            "pmi_momentum_3m",
            "breadth_ma20_pct",
        }

    def test_get_returns_correct_computer(self) -> None:
        """get() 按 factor_id 返回正确的计算器。"""
        registry = build_default_factor_registry()
        computer = registry.get("volume_ratio_20d")
        assert computer is not None
        assert computer.spec.factor_id == "volume_ratio_20d"

    def test_get_returns_none_for_unknown(self) -> None:
        """get() 未知 factor_id 返回 None。"""
        registry = build_default_factor_registry()
        assert registry.get("unknown_factor") is None

    def test_specs_returns_all_factor_specs(self) -> None:
        """specs() 应返回与 all() 等量的 FactorSpec 列表。"""
        registry = build_default_factor_registry()
        assert len(registry.specs()) == len(registry.all())


# ─── Sharpe60dComputer（风险调整动量）─────────────────────────────────────────────


class TestSharpe60dComputer:
    _computer = Sharpe60dComputer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "sharpe_60d"
        assert self._computer.spec.category == "momentum"

    def test_none_when_insufficient_data(self) -> None:
        """历史不足 61 条收盘价时应返回 None。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=30)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None

    def test_value_with_sufficient_data(self) -> None:
        """满足数据量时应返回正的风险调整动量（有波动的上涨序列）。"""
        trade_date = date(2024, 6, 1)
        # 构造有波动的上涨序列：整体上行但每日收益交替变化，避免波动率为 0
        bars = {}
        close = 100.0
        for i in range(65, 0, -1):
            dt = trade_date - timedelta(days=i)
            daily = 0.003 if i % 2 == 0 else 0.001
            close = close * (1 + daily)
            bars[("510300", dt)] = MockBar(close_price=round(close, 4))
        bars[("510300", trade_date)] = MockBar(close_price=round(close, 4))
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0
        assert result.payload.get("return_60d") is not None
        assert result.payload.get("volatility_20d") is not None

    def test_batch_matches_point(self) -> None:
        """批量计算结果应与逐点计算一致。"""
        trade_date = date(2024, 6, 1)
        bars = {}
        close = 100.0
        for i in range(65, 0, -1):
            dt = trade_date - timedelta(days=i)
            daily = 0.003 if i % 2 == 0 else 0.001
            close = close * (1 + daily)
            bars[("510300", dt)] = MockBar(close_price=round(close, 4))
        bars[("510300", trade_date)] = MockBar(close_price=round(close, 4))
        ctx = FactorContext(index_bars=bars)
        point = self._computer.compute("510300", trade_date, ctx)
        batch = self._computer.compute_batch("510300", [trade_date], ctx)[trade_date]
        assert point.numeric == batch.numeric


# ─── MADeviationComputer（均线乖离率）──────────────────────────────────────────────


class TestMADeviationComputer:
    _computer = MADeviationComputer(period=60)

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "ma60d_deviation"
        assert self._computer.spec.category == "technical"

    def test_none_when_insufficient_data(self) -> None:
        """历史不足 60 条收盘价时应返回 None。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=30)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None

    def test_positive_deviation_on_uptrend(self) -> None:
        """价格高于均线时乖离率为正。"""
        trade_date = date(2024, 6, 1)
        # 前 55 天横盘，后 10 天快速上涨 → 当前价高于 60 日均线
        bars = {}
        close = 100.0
        for i in range(65, 0, -1):
            dt = trade_date - timedelta(days=i)
            if i > 10:
                close = 100.0
            else:
                close = close * 1.01
            bars[("510300", dt)] = MockBar(close_price=round(close, 4))
        bars[("510300", trade_date)] = MockBar(close_price=round(close, 4))
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0

    def test_batch_matches_point(self) -> None:
        """批量计算结果应与逐点计算一致。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=65, daily_return=0.002)
        ctx = FactorContext(index_bars=bars)
        point = self._computer.compute("510300", trade_date, ctx)
        batch = self._computer.compute_batch("510300", [trade_date], ctx)[trade_date]
        assert point.numeric == batch.numeric


# ─── DrawdownCurrentComputer（当前回撤 + 水下时间）────────────────────────────────


class TestDrawdownCurrentComputer:
    _computer = DrawdownCurrentComputer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "drawdown_current"
        assert self._computer.spec.category == "technical"

    def test_none_when_insufficient_data(self) -> None:
        """历史不足 250 条收盘价时应返回 None。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=100)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None

    def test_drawdown_at_peak_is_zero(self) -> None:
        """当前价处于 250 日峰值时回撤应为 0。"""
        trade_date = date(2024, 6, 1)
        # 单调上涨 → 当前价即峰值
        bars = _build_index_bars("510300", trade_date, n_days=260, daily_return=0.001)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric == 0.0
        assert result.payload.get("underwater_days") == 0

    def test_drawdown_negative_after_peak(self) -> None:
        """从峰值回落时回撤为负，且水下天数大于 0。"""
        trade_date = date(2024, 6, 1)
        # 前 230 天横盘，再 20 天上涨到峰值，最后 10 天下跌
        bars = {}
        for i in range(260, 0, -1):
            dt = trade_date - timedelta(days=i)
            if i > 30:
                close = 100.0
            elif i > 10:
                close = 100.0 + (30 - i) * 1.0  # 上涨到 120
            else:
                close = 120.0 - (10 - i) * 1.0  # 下跌到 110
            bars[("510300", dt)] = MockBar(close_price=close)
        bars[("510300", trade_date)] = MockBar(close_price=110.0)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric < 0
        assert result.payload.get("underwater_days", 0) > 0

    def test_batch_matches_point(self) -> None:
        """批量计算结果应与逐点计算一致。"""
        trade_date = date(2024, 6, 1)
        bars = _build_index_bars("510300", trade_date, n_days=260, daily_return=0.001)
        ctx = FactorContext(index_bars=bars)
        point = self._computer.compute("510300", trade_date, ctx)
        batch = self._computer.compute_batch("510300", [trade_date], ctx)[trade_date]
        assert point.numeric == batch.numeric


# ─── AmountRatio20dComputer（成交额量比）──────────────────────────────────────────


class TestAmountRatio20dComputer:
    _computer = AmountRatio20dComputer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "amount_ratio_20d"
        assert self._computer.spec.category == "volume"

    def test_normal_compute(self) -> None:
        """正常场景：有足够历史成交额，量比应为正数。"""
        trade_date = date(2024, 6, 1)
        bars = {}
        for i in range(25, 0, -1):
            dt = trade_date - timedelta(days=i)
            bars[("510300", dt)] = MockBar(turnover=1000.0 + i)
        bars[("510300", trade_date)] = MockBar(turnover=3000.0)  # 当日放量
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 1.0

    def test_no_turnover_returns_none(self) -> None:
        """无成交额数据时应返回 None。"""
        ctx = FactorContext()
        result = self._computer.compute("510300", date(2024, 6, 1), ctx)
        assert result.numeric is None

    def test_batch_matches_point(self) -> None:
        """批量计算结果应与逐点计算一致。"""
        trade_date = date(2024, 6, 1)
        bars = {}
        for i in range(25, 0, -1):
            dt = trade_date - timedelta(days=i)
            bars[("510300", dt)] = MockBar(turnover=1000.0 + i)
        bars[("510300", trade_date)] = MockBar(turnover=3000.0)
        ctx = FactorContext(index_bars=bars)
        point = self._computer.compute("510300", trade_date, ctx)
        batch = self._computer.compute_batch("510300", [trade_date], ctx)[trade_date]
        assert point.numeric == batch.numeric


# ─── PMIMomentumComputer（PMI 三个月动量）─────────────────────────────────────────


class TestPMIMomentumComputer:
    _computer = PMIMomentumComputer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "pmi_momentum_3m"
        assert self._computer.spec.category == "macro"

    def test_none_when_no_macro(self) -> None:
        """无 PMI 数据时应返回 None。"""
        ctx = FactorContext()
        result = self._computer.compute("000300", date(2024, 6, 1), ctx)
        assert result.numeric is None

    def test_positive_momentum_when_pmi_rising(self) -> None:
        """PMI 上升时动量为正。"""
        trade_date = date(2024, 6, 1)
        ctx = FactorContext(
            macro_indicators={
                "pmi": {
                    "2023-03-01": 49.0,
                    "2023-04-01": 49.5,
                    "2023-05-01": 50.0,
                    "2023-06-01": 50.5,
                    "2024-05-01": 51.5,
                }
            }
        )
        result = self._computer.compute("000300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0

    def test_market_level_factor(self) -> None:
        """市场级因子：不同指数返回相同值。"""
        trade_date = date(2024, 6, 1)
        ctx = FactorContext(
            macro_indicators={
                "pmi": {
                    "2023-03-01": 49.0,
                    "2024-05-01": 51.0,
                }
            }
        )
        r1 = self._computer.compute("000300", trade_date, ctx)
        r2 = self._computer.compute("399673", trade_date, ctx)
        assert r1.numeric == r2.numeric


# ─── BreadthMA20Computer（市场宽度）────────────────────────────────────────────────


class TestBreadthMA20Computer:
    _computer = BreadthMA20Computer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "breadth_ma20_pct"
        assert self._computer.spec.market_scope is True

    def test_all_above_ma20(self) -> None:
        """全部指数站上 MA20 时宽度为 100。"""
        trade_date = date(2024, 6, 1)
        bars = {}
        for code in ("000300", "399673", "931743"):
            # 单调上涨 → 全部高于 MA20
            close = 100.0
            for i in range(25, 0, -1):
                dt = trade_date - timedelta(days=i)
                bars[(code, dt)] = MockBar(close_price=round(close, 4))
                close = close * 1.005
            bars[(code, trade_date)] = MockBar(close_price=round(close, 4))
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("000300", trade_date, ctx)
        assert result.numeric == 100.0
        assert result.payload.get("valid_indexes") == 3

    def test_half_above_ma20(self) -> None:
        """一半指数高于 MA20 时宽度约 50。"""
        trade_date = date(2024, 6, 1)
        bars = {}
        # 指数 A 上涨（高于 MA20）
        close = 100.0
        for i in range(25, 0, -1):
            dt = trade_date - timedelta(days=i)
            bars[("000300", dt)] = MockBar(close_price=round(close, 4))
            close = close * 1.005
        bars[("000300", trade_date)] = MockBar(close_price=round(close, 4))
        # 指数 B 横盘（接近 MA20 但略低）
        bars[("399673", trade_date)] = MockBar(close_price=100.0)
        for i in range(25, 0, -1):
            dt = trade_date - timedelta(days=i)
            bars[("399673", dt)] = MockBar(close_price=99.0)
        ctx = FactorContext(index_bars=bars)
        result = self._computer.compute("000300", trade_date, ctx)
        assert result.numeric is not None
        assert 40.0 < result.numeric < 60.0

    def test_no_data_returns_none(self) -> None:
        """无任何指数数据时应返回 None。"""
        ctx = FactorContext()
        result = self._computer.compute("000300", date(2024, 6, 1), ctx)
        assert result.numeric is None

    def test_batch_matches_point(self) -> None:
        """批量计算结果应与逐点计算一致。"""
        trade_date = date(2024, 6, 1)
        bars = {}
        for code in ("000300", "399673"):
            close = 100.0
            for i in range(25, 0, -1):
                dt = trade_date - timedelta(days=i)
                bars[(code, dt)] = MockBar(close_price=round(close, 4))
                close = close * 1.005
            bars[(code, trade_date)] = MockBar(close_price=round(close, 4))
        ctx = FactorContext(index_bars=bars)
        point = self._computer.compute("000300", trade_date, ctx)
        batch = self._computer.compute_batch("000300", [trade_date], ctx)[trade_date]
        assert point.numeric == batch.numeric
