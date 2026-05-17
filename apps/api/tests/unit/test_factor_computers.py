"""内置因子计算器单元测试。

测试策略参考 test_three_factor_factors.py：
- 构造 mock FactorContext（含模拟的 etf_bars / etf_shares dict）
- 测试每个 FactorComputer 的核心计算逻辑
- 覆盖：正常值、数据不足返回 None、公式验证、单调性
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.momentum import (
    Return20dComputer,
    Return5dComputer,
    Return60dComputer,
    _calc_nd_return,
)
from quant_etf_api.factors.builtins.share_flow import ShareDeltaPctComputer
from quant_etf_api.factors.builtins.volatility import Volatility20dComputer
from quant_etf_api.factors.builtins.volume import VolumeRatio20dComputer
from quant_etf_api.factors.registry import FactorRegistry, build_default_factor_registry


# ─── 测试辅助：轻量 mock 数据行 ─────────────────────────────────────────────────


@dataclass
class MockBar:
    """模拟 EtfDailyBarModel 行，仅保留计算所需字段。"""

    volume: float | None = None
    close_price: float | None = None
    change_pct: float | None = None


@dataclass
class MockShare:
    """模拟 EtfDailyShareModel 行，仅保留计算所需字段。"""

    shares_total: float | None = None
    shares_delta: float | None = None
    shares_delta_pct: float | None = None


def _build_etf_bars(
    etf_code: str,
    trade_date: date,
    n_days: int,
    base_volume: float = 1000.0,
    base_close: float = 100.0,
    daily_return: float = 0.001,
) -> dict:
    """构造指定 ETF 的 n_days 天历史 K 线数据（含 trade_date 当日）。

    从 trade_date 向前推 n_days-1 天，收盘价按 daily_return 递增。

    Args:
        etf_code: ETF 代码。
        trade_date: 最新交易日。
        n_days: 生成的历史天数（含 trade_date 当日）。
        base_volume: 最早一日的成交量。
        base_close: 最早一日的收盘价。
        daily_return: 每日收益率，用于模拟价格序列。

    Returns:
        符合 FactorContext.etf_bars 格式的 dict。
    """
    bars = {}
    close = base_close
    for i in range(n_days - 1, -1, -1):
        dt = trade_date - timedelta(days=i)
        bars[(etf_code, dt)] = MockBar(
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
        bars = _build_etf_bars("510300", trade_date, n_days=25)
        ctx = FactorContext(etf_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0

    def test_no_data_returns_default(self) -> None:
        """无任何数据时应返回默认值 1.0（_bar_metrics 的 fallback）。"""
        ctx = FactorContext()
        result = self._computer.compute("510300", date(2024, 6, 1), ctx)
        assert result.numeric == 1.0

    def test_missing_today_bar(self) -> None:
        """当日无 K 线时返回默认值 1.0。"""
        trade_date = date(2024, 6, 1)
        # 只有前一天的数据，没有当日
        bars = _build_etf_bars("510300", trade_date - timedelta(days=1), n_days=20)
        ctx = FactorContext(etf_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric == 1.0

    def test_payload_contains_lookback(self) -> None:
        """payload 中应包含 lookback_days 字段。"""
        trade_date = date(2024, 6, 1)
        bars = _build_etf_bars("510300", trade_date, n_days=25)
        ctx = FactorContext(etf_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.payload.get("lookback_days") == 20


# ─── _calc_nd_return 通用函数 ─────────────────────────────────────────────────────


class TestCalcNdReturn:
    def test_returns_none_when_no_today_bar(self) -> None:
        ctx = FactorContext()
        assert _calc_nd_return("510300", date(2024, 6, 1), ctx, n=5) is None

    def test_returns_none_when_insufficient_history(self) -> None:
        trade_date = date(2024, 6, 1)
        bars = _build_etf_bars("510300", trade_date, n_days=4)  # 只有3条历史
        ctx = FactorContext(etf_bars=bars)
        assert _calc_nd_return("510300", trade_date, ctx, n=5) is None

    def test_positive_return_on_rising_prices(self) -> None:
        """价格单调上涨时，收益率应为正。"""
        trade_date = date(2024, 6, 1)
        bars = _build_etf_bars("510300", trade_date, n_days=10, daily_return=0.01)
        ctx = FactorContext(etf_bars=bars)
        result = _calc_nd_return("510300", trade_date, ctx, n=5)
        assert result is not None
        assert result > 0

    def test_negative_return_on_falling_prices(self) -> None:
        """价格单调下跌时，收益率应为负。"""
        trade_date = date(2024, 6, 1)
        bars = _build_etf_bars("510300", trade_date, n_days=10, daily_return=-0.01)
        ctx = FactorContext(etf_bars=bars)
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
        bars = _build_etf_bars("510300", trade_date, n_days=10, daily_return=0.01)
        ctx = FactorContext(etf_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 4.0  # 每日 1%，5日约 5%

    def test_none_when_insufficient_data(self) -> None:
        """历史数据不足5条时应返回 None。"""
        trade_date = date(2024, 6, 1)
        bars = _build_etf_bars("510300", trade_date, n_days=3)  # 只有2条历史
        ctx = FactorContext(etf_bars=bars)
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
        bars = _build_etf_bars("510300", trade_date, n_days=15)
        ctx = FactorContext(etf_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None

    def test_returns_value_with_sufficient_data(self) -> None:
        """满足21条数据时应返回非 None 值。"""
        trade_date = date(2024, 6, 1)
        bars = _build_etf_bars("510300", trade_date, n_days=25, daily_return=0.002)
        ctx = FactorContext(etf_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0

    def test_monotone_property(self) -> None:
        """n=5 的收益率应小于 n=20 的收益率（同等每日涨幅下，时间窗口越长收益越大）。"""
        trade_date = date(2024, 6, 1)
        bars = _build_etf_bars("510300", trade_date, n_days=25, daily_return=0.01)
        ctx = FactorContext(etf_bars=bars)
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
        bars = _build_etf_bars("510300", trade_date, n_days=30)
        ctx = FactorContext(etf_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None

    def test_returns_value_with_sufficient_data(self) -> None:
        """满足61条数据时应返回非 None 值。"""
        trade_date = date(2024, 6, 1)
        bars = _build_etf_bars("510300", trade_date, n_days=65, daily_return=0.002)
        ctx = FactorContext(etf_bars=bars)
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
        bars = _build_etf_bars("510300", trade_date, n_days=10)
        ctx = FactorContext(etf_bars=bars)
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
        ctx = FactorContext(etf_bars=bars)
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
        ctx_low = FactorContext(etf_bars=bars_low)
        result_low = self._computer.compute("510300", trade_date, ctx_low)

        # 高波动：交替 +3%/-3%，25条
        closes_high = [100.0]
        for i in range(24):
            closes_high.append(closes_high[-1] * (1.03 if i % 2 == 0 else 0.97))
        bars_high = {
            ("510300", trade_date - timedelta(days=24 - i)): MockBar(close_price=closes_high[i])
            for i in range(25)
        }
        ctx_high = FactorContext(etf_bars=bars_high)
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
        ctx = FactorContext(etf_bars=bars)
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
        ctx = FactorContext(etf_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric > 0

    def test_payload_contains_sample_count(self) -> None:
        """payload 中应包含 sample_count 字段。"""
        trade_date = date(2024, 6, 1)
        bars = _build_etf_bars("510300", trade_date, n_days=25)
        ctx = FactorContext(etf_bars=bars)
        result = self._computer.compute("510300", trade_date, ctx)
        assert "sample_count" in result.payload


# ─── ShareDeltaPctComputer ────────────────────────────────────────────────────────


class TestShareDeltaPctComputer:
    _computer = ShareDeltaPctComputer()

    def test_spec(self) -> None:
        assert self._computer.spec.factor_id == "share_delta_pct"
        assert self._computer.spec.category == "flow"

    def test_no_share_data(self) -> None:
        """无份额数据时应返回 None，payload 说明原因。"""
        ctx = FactorContext()
        result = self._computer.compute("510300", date(2024, 6, 1), ctx)
        assert result.numeric is None
        assert "无份额数据" in result.payload.get("reason", "")

    def test_positive_inflow(self) -> None:
        """净申购时 numeric > 0。"""
        trade_date = date(2024, 6, 1)
        ctx = FactorContext(
            etf_shares={
                ("510300", trade_date): MockShare(
                    shares_total=100.0,
                    shares_delta=5.0,
                    shares_delta_pct=5.26,
                )
            }
        )
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric == pytest.approx(5.26)

    def test_negative_outflow(self) -> None:
        """净赎回时 numeric < 0。"""
        trade_date = date(2024, 6, 1)
        ctx = FactorContext(
            etf_shares={
                ("510300", trade_date): MockShare(
                    shares_total=100.0,
                    shares_delta=-3.0,
                    shares_delta_pct=-2.91,
                )
            }
        )
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is not None
        assert result.numeric < 0

    def test_none_delta_pct(self) -> None:
        """shares_delta_pct 为 None 时（数据存在但值缺失），numeric 应为 None。"""
        trade_date = date(2024, 6, 1)
        ctx = FactorContext(
            etf_shares={
                ("510300", trade_date): MockShare(
                    shares_total=100.0,
                    shares_delta=None,
                    shares_delta_pct=None,
                )
            }
        )
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.numeric is None

    def test_payload_contains_shares_info(self) -> None:
        """payload 中应包含 shares_total 和 shares_delta。"""
        trade_date = date(2024, 6, 1)
        ctx = FactorContext(
            etf_shares={
                ("510300", trade_date): MockShare(
                    shares_total=100.0,
                    shares_delta=2.0,
                    shares_delta_pct=2.04,
                )
            }
        )
        result = self._computer.compute("510300", trade_date, ctx)
        assert result.payload.get("shares_total") == 100.0
        assert result.payload.get("shares_delta") == 2.0


# ─── FactorRegistry ──────────────────────────────────────────────────────────────


class TestFactorRegistry:
    def test_default_registry_has_six_factors(self) -> None:
        """默认注册表应包含 6 个内置因子。"""
        registry = build_default_factor_registry()
        assert len(registry.all()) == 6

    def test_default_registry_factor_ids(self) -> None:
        """默认注册表的 factor_id 集合应完整。"""
        registry = build_default_factor_registry()
        ids = {c.spec.factor_id for c in registry.all()}
        assert ids == {
            "volume_ratio_20d",
            "return_5d",
            "return_20d",
            "return_60d",
            "volatility_20d",
            "share_delta_pct",
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
