"""宏观 / 日频市场指标序列因子（市场级，对所有指数取同一值）。

两类上游数据都经 ``FactorContext.macro_series`` 读取（按可见日排序，只用截止当日已可见
的数据）：

- ``macro_series``：月度宏观指标（CPI、PMI、M1/M2 同比、社融存量等），可见日按发布滞后
  推导（见 ``factors.macro_period``）；
- ``market_series``：日频市场指标（10 年国债收益率、两融、股指期货基差、股指期权 PCR），
  交易日当日可见。最新观测距决策日超过 ``_MAX_STALE_DAYS`` 个自然日视为数据中断，取缺失。
"""

from __future__ import annotations

import bisect
import math
from datetime import date, timedelta

from quant_etf_api.factors.base import (
    VALUE_SHAPE_MARKET,
    FactorContext,
    FactorSpec,
    FactorValue,
)

# 月度指标回看按每月 30 天折算
_DAYS_PER_MONTH = 30
# 日频指标最新观测允许的最大陈旧天数（覆盖长假）
_MAX_STALE_DAYS = 15
# 窗口均值要求的有效观测占比
_WINDOW_VALID_RATIO = 0.8

SERIES_MODES = ("level", "change", "pct_change")


def _transform(latest: float, base: float | None, mode: str) -> float | None:
    """按模式把最新值与基期值转换为因子值。"""
    if mode == "level":
        return latest
    if base is None:
        return None
    if mode == "change":
        return latest - base
    if base == 0:
        return None
    return (latest / base - 1) * 100


class MacroSeriesComputer:
    """月度宏观指标：最新已公布值，或相对约 N 个月前的变化 / 变化率。"""

    def __init__(self, code: str, mode: str, months: int) -> None:
        """初始化。

        Args:
            code: 宏观指标代码。
            mode: level / change / pct_change。
            months: 变化的回看月数（level 时忽略）。
        """
        self._code = code
        self._mode = mode
        self._months = int(months)

    @property
    def spec(self) -> FactorSpec:
        """因子元数据（模板注册时以模板描述为准）。"""
        return FactorSpec(
            factor_id="macro_series",
            name="宏观指标序列",
            category="macro",
            version="1.0.0",
            description="宏观指标序列",
            required_data=["macro_indicators"],
            lookback_days=macro_series_lookback_days(self._months),
            value_shape=VALUE_SHAPE_MARKET,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """单日计算（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """逐日取截止当日已公布的最新一期并按模式转换。

        Args:
            index_code: 指数代码（市场级，不参与计算）。
            dates: 交易日。
            ctx: 含宏观指标的上下文。

        Returns:
            交易日 → FactorValue。
        """
        records = ctx.macro_series(self._code)
        available = [record[0] for record in records]
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            visible = records[: bisect.bisect_right(available, trade_date)]
            numeric = None
            if visible:
                _, latest_period, _, latest_value = visible[-1]
                target = latest_period - timedelta(days=_DAYS_PER_MONTH * self._months)
                base = None
                for _, period_date, _, value in visible:
                    if period_date <= target:
                        base = value
                    else:
                        break
                value = _transform(latest_value, base, self._mode)
                numeric = None if value is None else round(value, 6)
            result[trade_date] = FactorValue(factor_id="macro_series", numeric=numeric)
        return result


def macro_series_lookback_days(months: int) -> int:
    """月度序列回望自然日：回看月数 × 2 个月余量，至少 120 天、至多 730 天。

    宏观指标总是全量历史加载（与回望无关），回望只影响行情预加载，因此封顶在回测
    行情预加载窗口内。

    Args:
        months: 回看月数。

    Returns:
        回望自然日数。
    """
    return min(730, max(120, int(months) * _DAYS_PER_MONTH * 2))


class MarketSeriesComputer:
    """日频市场指标：窗口均值，或相对 N 个交易日前的变化 / 变化率。"""

    def __init__(self, code: str, mode: str, window: int) -> None:
        """初始化。

        Args:
            code: 市场指标代码。
            mode: level（窗口均值）/ change / pct_change。
            window: 窗口（交易日）。
        """
        self._code = code
        self._mode = mode
        self._window = int(window)

    @property
    def spec(self) -> FactorSpec:
        """因子元数据（模板注册时以模板描述为准）。"""
        return FactorSpec(
            factor_id="market_series",
            name="日频市场指标序列",
            category="market",
            version="1.0.0",
            description="日频市场指标序列",
            required_data=["macro_indicators"],
            lookback_days=market_series_lookback_days(self._window),
            value_shape=VALUE_SHAPE_MARKET,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """单日计算（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """逐日取截止当日的观测并按模式转换。

        Args:
            index_code: 指数代码（市场级，不参与计算）。
            dates: 交易日。
            ctx: 含市场指标的上下文。

        Returns:
            交易日 → FactorValue。
        """
        records = ctx.macro_series(self._code)
        available = [record[0] for record in records]
        values = [record[3] for record in records]
        need = max(1, math.ceil(self._window * _WINDOW_VALID_RATIO))
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            end = bisect.bisect_right(available, trade_date)
            numeric = None
            fresh = end > 0 and (trade_date - available[end - 1]).days <= _MAX_STALE_DAYS
            if fresh:
                if self._mode == "level":
                    window = values[max(0, end - self._window) : end]
                    if len(window) >= need:
                        numeric = sum(window) / len(window)
                elif end > self._window:
                    numeric = _transform(
                        values[end - 1], values[end - 1 - self._window], self._mode
                    )
            result[trade_date] = FactorValue(
                factor_id="market_series",
                numeric=None if numeric is None else round(numeric, 6),
            )
        return result


def market_series_lookback_days(window: int) -> int:
    """日频序列回望自然日：窗口折算 + 余量。

    Args:
        window: 窗口（交易日）。

    Returns:
        回望自然日数。
    """
    return int(window * 1.6) + 30
