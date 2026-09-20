"""波动率类因子：年化波动率、收益率标准差与区间宽度（基于指数数据）。

包含三类因子，均按周期参数化：N 日日收益率年化标准差、N 日日收益率标准差
（%，不年化，Tushare return_std_* 口径）与 N 日区间宽度
（%，Tushare high_low_* 口径）。

三者均实现 BatchFactorComputer 协议，回测预计算时一次遍历全量 bar 数据
覆盖所有交易日，避免逐日重复构建收盘价序列。
"""

from __future__ import annotations

import bisect
import math
from datetime import date

from quant_etf_api.factors.base import (
    FactorContext,
    FactorSpec,
    FactorValue,
    period_lookback_days,
)

# A 股全年约 252 个交易日，年化因子为 sqrt(252)
_ANNUALIZE_FACTOR = math.sqrt(252)


def _sorted_closes_for_code(
    index_code: str,
    ctx: FactorContext,
) -> tuple[list[date], list[float]]:
    """提取指定指数的有序收盘价序列，供批量计算复用。

    Args:
        index_code: 指数代码。
        ctx: FactorContext，包含全量回望数据。

    Returns:
        (close_dates, close_prices) 元组，均按日期升序排列。
    """
    closes = sorted(
        [
            (dt, v.close_price)
            for (code, dt), v in ctx.index_bars.items()
            if code == index_code and v.close_price is not None
        ],
        key=lambda x: x[0],
    )
    if not closes:
        return [], []
    return [d for d, _ in closes], [p for _, p in closes]


def _sample_std(values: list[float]) -> float | None:
    """计算样本标准差（ddof=1）。

    Args:
        values: 数值序列。

    Returns:
        样本标准差，样本数不足 2 时返回 None。
    """
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)


def daily_returns(window: list[float]) -> list[float]:
    """由收盘价窗口计算日收益率序列。

    以窗口内非正的前一日收盘价作为基准的收益率无法定义，直接跳过。

    Args:
        window: 从旧到新的连续收盘价。

    Returns:
        日收益率列表（小数，非百分比），长度为 len(window) - 有效基准数。
    """
    return [
        (window[i] - window[i - 1]) / window[i - 1]
        for i in range(1, len(window))
        if window[i - 1] > 0
    ]


def daily_return_std(window: list[float]) -> float | None:
    """计算收盘价窗口内日收益率的样本标准差（ddof=1）。

    Args:
        window: 从旧到新的连续收盘价。

    Returns:
        日收益率标准差（小数），有效日收益率不足 2 个时返回 None。
    """
    return _sample_std(daily_returns(window))


def annualized_volatility(window: list[float]) -> float | None:
    """计算收盘价窗口内日收益率的年化标准差（%）。

    年化口径：std(日收益率, ddof=1) × sqrt(252) × 100。

    Args:
        window: 从旧到新的连续收盘价。

    Returns:
        年化波动率（%），有效日收益率不足 2 个时返回 None。
    """
    std_dev = daily_return_std(window)
    if std_dev is None:
        return None
    return round(std_dev * _ANNUALIZE_FACTOR * 100, 4)


def _window_bounds(
    close_dates: list[date],
    close_prices: list[float],
    trade_date: date,
    n: int,
) -> list[float] | None:
    """取截至 trade_date 的最近 n 个收盘价窗口（含当日）。

    当日无 bar、日期不对齐或历史不足 n 条时返回 None，避免把陈旧窗口
    误当成目标交易日的因子值。

    Args:
        close_dates: 已排序的收盘价日期列表。
        close_prices: 对应收盘价列表。
        trade_date: 目标交易日。
        n: 需要的收盘价数量。

    Returns:
        从旧到新的 n 个收盘价，数据不足时返回 None。
    """
    idx = bisect.bisect_right(close_dates, trade_date) - 1
    if idx < 0 or close_dates[idx] != trade_date or idx < n - 1:
        return None
    return close_prices[idx - n + 1 : idx + 1]


class VolatilityComputer:
    """N 日年化波动率因子计算器。

    计算公式：std(近 N 个日收益率, ddof=1) × sqrt(252) × 100。
    使用样本标准差（除以 n-1，贝塞尔修正），与金融实践一致。
    需要 N+1 个连续收盘价（含当日）才能算出 N 个日收益率。
    结果单位为 %（年化标准差 × 100）。
    实现 BatchFactorComputer 协议，支持回测批量预计算。

    Attributes:
        _period: 回望交易日数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 20) -> None:
        """初始化年化波动率计算器。

        Args:
            period: 回望交易日数，如 17/20/60。

        Raises:
            ValueError: period 小于 2，无法构成日收益率样本。
        """
        if period < 2:
            raise ValueError("period 必须至少为 2")
        self._period = int(period)
        self._lookback = period_lookback_days(self._period)

    @property
    def spec(self) -> FactorSpec:
        """返回 N 日年化波动率的因子元数据。"""
        return FactorSpec(
            factor_id=f"volatility_{self._period}d",
            name=f"{self._period}日年化波动率",
            category="volatility",
            version="2.0.0",
            description=(
                f"指数近 {self._period} 个交易日日收益率的年化标准差（%）。"
                f"公式：std({self._period} 个日收益率, ddof=1) × sqrt(252) × 100。"
                f"需 {self._period + 1} 个连续收盘价。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 N 日年化波动率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，需 period+1 个收盘价，不足时 numeric 为 None；
            payload 包含 sample_count（实际使用的日收益率数量）与 std_daily。
        """
        close_dates, close_prices = _sorted_closes_for_code(index_code, ctx)
        window = _window_bounds(close_dates, close_prices, trade_date, self._period + 1)
        return self._build_value(window)

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 N 日年化波动率。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _sorted_closes_for_code(index_code, ctx)
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            window = _window_bounds(
                close_dates, close_prices, trade_date, self._period + 1
            )
            result[trade_date] = self._build_value(window)
        return result

    def _build_value(self, window: list[float] | None) -> FactorValue:
        """由 period+1 个收盘价窗口构造年化波动率因子值。

        Args:
            window: 从旧到新的 period+1 个收盘价，None 表示数据不足。

        Returns:
            FactorValue，日收益率样本不足 2 个时 numeric 为 None。
        """
        if window is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "reason": f"收盘价数据不足 {self._period + 1} 条或当日无行情",
                    "period": self._period,
                    "required": self._period + 1,
                },
            )
        returns = daily_returns(window)
        std_dev = _sample_std(returns)
        if std_dev is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"period": self._period, "sample_count": len(returns), "required": 2},
            )
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(std_dev * _ANNUALIZE_FACTOR * 100, 4),
            payload={
                "period": self._period,
                "sample_count": len(returns),
                "std_daily": round(std_dev, 6),
            },
        )


class ReturnStdComputer:
    """N 日日收益率标准差因子计算器（不年化）。

    计算公式：std(近 N 个日收益率, ddof=1) × 100。
    与 VolatilityComputer 的区别：不做 sqrt(252) 年化，口径对应
    Tushare 因子库的 return_std_* 系列，可直接用于横截面比较。
    需要 N+1 个连续收盘价才能算出 N 个日收益率。
    实现 BatchFactorComputer 协议，支持回测批量预计算。

    Attributes:
        _period: 回望交易日数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 63) -> None:
        """初始化收益率标准差计算器。

        Args:
            period: 回望交易日数，如 21/42/63/126/252。
        """
        self._period = period
        self._lookback = period_lookback_days(period)

    @property
    def spec(self) -> FactorSpec:
        """返回 N 日日收益率标准差的因子元数据。"""
        return FactorSpec(
            factor_id=f"return_std_{self._period}d",
            name=f"{self._period}日收益率标准差",
            category="volatility",
            version="1.0.0",
            description=(
                f"指数近 {self._period} 个交易日日收益率的标准差（%，不年化）。"
                f"公式：std({self._period} 个日收益率, ddof=1) × 100，"
                f"需 {self._period + 1} 个连续收盘价。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 N 日日收益率标准差。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，数据不足时 numeric 为 None；
            payload 包含 sample_count（实际使用的日收益率数量）。
        """
        close_dates, close_prices = _sorted_closes_for_code(index_code, ctx)
        window = _window_bounds(close_dates, close_prices, trade_date, self._period + 1)
        if window is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "reason": f"收盘价数据不足 {self._period + 1} 条或当日无行情",
                    "required": self._period + 1,
                },
            )
        return self._build_value(window)

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 N 日日收益率标准差。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _sorted_closes_for_code(index_code, ctx)
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            window = _window_bounds(
                close_dates, close_prices, trade_date, self._period + 1
            )
            if window is None:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={
                        "reason": f"收盘价数据不足 {self._period + 1} 条或当日无行情",
                        "required": self._period + 1,
                    },
                )
                continue
            result[trade_date] = self._build_value(window)
        return result

    def _build_value(self, window: list[float]) -> FactorValue:
        """由 N+1 个收盘价窗口构造因子值。

        Args:
            window: 从旧到新的 N+1 个收盘价。

        Returns:
            FactorValue，日收益率样本不足 2 个时 numeric 为 None。
        """
        returns = daily_returns(window)
        std_dev = _sample_std(returns)
        if std_dev is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"period": self._period, "sample_count": len(returns), "required": 2},
            )
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(std_dev * 100, 4),
            payload={"period": self._period, "sample_count": len(returns)},
        )


class HighLowRangeComputer:
    """N 日区间宽度因子计算器。

    计算公式：(近 N 个交易日最高收盘价 − 最低收盘价) / 最低收盘价 × 100。
    数值越大代表区间内价格波动幅度越大，口径对应 Tushare 因子库的
    high_low_* 系列（净值曲线最高点与最低点比值，此处换算为百分比宽度）。
    实现 BatchFactorComputer 协议，支持回测批量预计算。

    Attributes:
        _period: 回望交易日数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 63) -> None:
        """初始化区间宽度计算器。

        Args:
            period: 回望交易日数，如 21/42/63/126/252。
        """
        self._period = period
        self._lookback = period_lookback_days(period)

    @property
    def spec(self) -> FactorSpec:
        """返回 N 日区间宽度的因子元数据。"""
        return FactorSpec(
            factor_id=f"high_low_{self._period}d",
            name=f"{self._period}日区间宽度",
            category="volatility",
            version="1.0.0",
            description=(
                f"指数近 {self._period} 个交易日最高收盘与最低收盘之间的区间宽度（%）。"
                f"公式：(最高收盘 − 最低收盘) / 最低收盘 × 100。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 N 日区间宽度。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，数据不足时 numeric 为 None；
            payload 包含 window_high / window_low。
        """
        close_dates, close_prices = _sorted_closes_for_code(index_code, ctx)
        window = _window_bounds(close_dates, close_prices, trade_date, self._period)
        return self._build_value(window, close_dates, close_prices, trade_date)

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 N 日区间宽度。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _sorted_closes_for_code(index_code, ctx)
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            window = _window_bounds(close_dates, close_prices, trade_date, self._period)
            result[trade_date] = self._build_value(
                window, close_dates, close_prices, trade_date
            )
        return result

    def _build_value(
        self,
        window: list[float] | None,
        close_dates: list[date],
        close_prices: list[float],
        trade_date: date,
    ) -> FactorValue:
        """由 N 个收盘价窗口构造区间宽度因子值。

        Args:
            window: 从旧到新的 N 个收盘价，None 表示数据不足。
            close_dates: 已排序的收盘价日期列表（仅用于判定当日对齐）。
            close_prices: 对应收盘价列表（仅用于判定当日对齐）。
            trade_date: 目标交易日。

        Returns:
            FactorValue，数据不足或最低价非正时 numeric 为 None。
        """
        if window is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "reason": f"收盘价数据不足 {self._period} 条或当日无行情",
                    "required": self._period,
                },
            )
        highest = max(window)
        lowest = min(window)
        if lowest <= 0:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "窗口内最低收盘价非正", "required": self._period},
            )
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round((highest - lowest) / lowest * 100, 4),
            payload={
                "window_high": round(highest, 6),
                "window_low": round(lowest, 6),
                "sample_count": len(window),
            },
        )

