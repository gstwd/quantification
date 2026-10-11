"""技术指标类因子：均线、均线乖离、ATR、Donchian 通道、RSI、回撤（基于指数数据）。

补充趋势类和通道类因子的缺失，支持双均线、海龟交易、RSI 超买超卖等经典策略。

全部计算器实现 BatchFactorComputer 协议：逐点 ``compute`` 直接委托 ``compute_batch``，
保证两条路径口径完全一致；有序序列取自 FactorContext 的按指数缓存，避免逐日、
逐因子重复扫描全量 bar。
"""

from __future__ import annotations

import math
from datetime import date

from quant_etf_api.factors.base import (
    FactorContext,
    FactorSpec,
    FactorValue,
    index_on,
    period_lookback_days,
)

# ATR / Donchian 共用的高、低、收盘价字段（三者任一缺失的日线不参与计算）
_HLC_FIELDS = ("high_price", "low_price", "close_price")


def _build_sorted_closes_for_code(
    index_code: str,
    ctx: FactorContext,
) -> tuple[list[date], list[float]]:
    """提取指定指数的有序收盘价序列（复用上下文缓存）。

    Args:
        index_code: 指数代码。
        ctx: FactorContext，包含全量回望数据。

    Returns:
        (close_dates, close_prices) 元组，均按日期升序排列。
    """
    return ctx.closes(index_code)


def _build_sorted_hlc_for_code(
    index_code: str,
    ctx: FactorContext,
) -> tuple[list[date], list[tuple[float, float, float]]]:
    """提取指定指数高、低、收盘价均非空的有序序列（复用上下文缓存）。

    Args:
        index_code: 指数代码。
        ctx: FactorContext，包含全量回望数据。

    Returns:
        (dates, [(high, low, close), ...]) 元组，按日期升序排列。
    """
    return ctx.series(index_code, _HLC_FIELDS)


# ══════════════════════════════════════════════════════════════════════
# 均线因子
# ══════════════════════════════════════════════════════════════════════


class MAComputer:
    """移动均线因子计算器。

    计算指定周期的简单移动平均（SMA）。输出为绝对点位，只适合与同一指数的
    价格比较（择时/过滤），不宜做横截面比较。

    Attributes:
        _period: 均线周期（交易日数）。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 20) -> None:
        """初始化均线计算器。

        Args:
            period: 均线周期（交易日数），如 5/10/20/60。
        """
        self._period = period
        self._lookback = period_lookback_days(period)

    @property
    def spec(self) -> FactorSpec:
        """返回移动均线的因子元数据。"""
        return FactorSpec(
            factor_id=f"ma_{self._period}d",
            name=f"{self._period}日均线",
            category="technical",
            version="1.0.0",
            description=f"指数近 {self._period} 个交易日收盘价的简单移动平均。",
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算移动均线（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的移动均线。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        result: dict[date, FactorValue] = {}
        close_dates, close_prices = _build_sorted_closes_for_code(index_code, ctx)
        for trade_date in dates:
            idx = index_on(close_dates, trade_date)
            if idx is None or idx < self._period - 1:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": f"收盘价数据不足 {self._period} 条"},
                )
                continue
            window = close_prices[idx - self._period + 1 : idx + 1]
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(sum(window) / len(window), 4),
                payload={"period": self._period, "sample_count": len(window)},
            )
        return result


# ══════════════════════════════════════════════════════════════════════
# 均线乖离率因子
# ══════════════════════════════════════════════════════════════════════


class MADeviationComputer:
    """均线乖离率因子计算器。

    计算当前收盘价相对指定周期均线的偏离百分比（%）。
    乖离率 = (close - ma_N) / ma_N × 100，正值表示价格位于均线上方。
    与现有 trend_score 变换配套使用：trend_score 期望输入
    "价格相对 MA60 偏离度"，本因子即为该输入。

    Attributes:
        _period: 均线周期（交易日数）。
    """

    def __init__(self, period: int = 60) -> None:
        """初始化均线乖离率计算器。

        Args:
            period: 均线周期（交易日数），默认 60。
        """
        self._period = period

    @property
    def spec(self) -> FactorSpec:
        """返回均线乖离率的因子元数据。"""
        return FactorSpec(
            factor_id=f"ma{self._period}d_deviation",
            name=f"{self._period}日均线乖离率",
            category="technical",
            version="1.0.0",
            description=(
                f"当前收盘价相对 {self._period} 日均线的偏离百分比（%），"
                "正值表示价格位于均线上方。可用于趋势强度判断。"
            ),
            required_data=["index_bars"],
            lookback_days=period_lookback_days(self._period),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算均线乖离率（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的均线乖离率。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        result: dict[date, FactorValue] = {}
        close_dates, close_prices = _build_sorted_closes_for_code(index_code, ctx)
        for trade_date in dates:
            idx = index_on(close_dates, trade_date)
            if idx is None or idx < self._period - 1:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": f"收盘价数据不足 {self._period} 条"},
                )
                continue
            window = close_prices[idx - self._period + 1 : idx + 1]
            ma = sum(window) / len(window)
            if ma <= 0:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": "均线值异常"},
                )
                continue
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round((close_prices[idx] - ma) / ma * 100, 4),
                payload={
                    "period": self._period,
                    "ma": round(ma, 4),
                    "close": round(close_prices[idx], 4),
                },
            )
        return result


# ══════════════════════════════════════════════════════════════════════
# ATR 因子（Average True Range）
# ══════════════════════════════════════════════════════════════════════


class ATRComputer:
    """平均真实波幅（ATR）因子计算器。

    默认 14 日 ATR，用于衡量市场波动程度，
    是海龟交易法则中仓位管理和止损设置的核心参数。输出为绝对点位，
    不宜做横截面比较。

    True Range = max(H-L, |H-prev_C|, |L-prev_C|)
    ATR = mean(TR_1, ..., TR_N)
    """

    def __init__(self, period: int = 14) -> None:
        """初始化 ATR 计算器。

        Args:
            period: ATR 计算周期（交易日数），默认 14。
        """
        self._period = period

    @property
    def spec(self) -> FactorSpec:
        """返回 ATR 的因子元数据。"""
        return FactorSpec(
            factor_id=f"atr_{self._period}d",
            name=f"{self._period}日ATR",
            category="technical",
            version="1.0.0",
            description=(
                f"指数近 {self._period} 个交易日的平均真实波幅（ATR），"
                "衡量市场波动程度。TR = max(H-L, |H-prevC|, |L-prevC|)。"
            ),
            required_data=["index_bars"],
            lookback_days=period_lookback_days(self._period),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 ATR（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 ATR。

        需要 period+1 条高低收完整的日线以计算 period 个 True Range。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        result: dict[date, FactorValue] = {}
        bar_dates, bars = _build_sorted_hlc_for_code(index_code, ctx)
        for trade_date in dates:
            idx = index_on(bar_dates, trade_date)
            if idx is None or idx < self._period:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": f"日线数据不足 {self._period + 1} 条"},
                )
                continue
            window = bars[idx - self._period : idx + 1]
            tr_list = [
                max(high - low, abs(high - prev_close), abs(low - prev_close))
                for (_, _, prev_close), (high, low, _) in zip(window, window[1:])
            ]
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(sum(tr_list) / len(tr_list), 4),
                payload={"period": self._period, "tr_count": len(tr_list)},
            )
        return result


# ══════════════════════════════════════════════════════════════════════
# Donchian 通道因子
# ══════════════════════════════════════════════════════════════════════


class _DonchianComputer:
    """Donchian 通道公共实现：取当日之前 N 个交易日的最高/最低价。

    通道不含当日：经典海龟突破规则比较「今日价格」与「此前 N 日极值」，
    若窗口包含当日，收盘价不可能高于当日最高价，``close > 上轨`` 永不成立。
    """

    _side = "high"

    def __init__(self, period: int = 20) -> None:
        """初始化 Donchian 通道计算器。

        Args:
            period: 回望周期（交易日数），默认 20。
        """
        self._period = period

    @property
    def spec(self) -> FactorSpec:
        """返回 Donchian 通道的因子元数据。"""
        is_high = self._side == "high"
        return FactorSpec(
            factor_id=f"donchian_{self._period}d_{self._side}",
            name=f"{self._period}日通道{'上' if is_high else '下'}轨",
            category="technical",
            version="2.0.0",
            description=(
                f"指数前 {self._period} 个交易日（不含当日）的"
                f"{'最高价，用于突破策略' if is_high else '最低价，用于止损策略'}。"
            ),
            required_data=["index_bars"],
            lookback_days=period_lookback_days(self._period),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 Donchian 通道（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 Donchian 通道。

        要求当日有高低收完整的日线，且此前至少有 period 条完整日线。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        result: dict[date, FactorValue] = {}
        bar_dates, bars = _build_sorted_hlc_for_code(index_code, ctx)
        for trade_date in dates:
            idx = index_on(bar_dates, trade_date)
            if idx is None or idx < self._period:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": f"日线数据不足 {self._period + 1} 条"},
                )
                continue
            window = bars[idx - self._period : idx]
            if self._side == "high":
                value = max(high for high, _, _ in window)
            else:
                value = min(low for _, low, _ in window)
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=value,
                payload={"period": self._period, "include_today": False},
            )
        return result


class DonchianHighComputer(_DonchianComputer):
    """Donchian 通道上轨：此前 N 个交易日的最高价（不含当日），用于突破买入信号。"""

    _side = "high"


class DonchianLowComputer(_DonchianComputer):
    """Donchian 通道下轨：此前 N 个交易日的最低价（不含当日），用于跌破止损信号。"""

    _side = "low"


# ══════════════════════════════════════════════════════════════════════
# RSI 因子（Relative Strength Index）
# ══════════════════════════════════════════════════════════════════════


class RSIComputer:
    """相对强弱指标（RSI）因子计算器。

    采用简单平均口径（Cutler RSI）：RS = 近 N 日平均涨幅 / 平均跌幅，
    RSI = 100 - 100 / (1 + RS)。不采用 Wilder 递推平滑，是因为递推结果依赖
    序列起点，会让实时（固定回望）与回测（长历史）口径不一致。
    取值 0-100，>70 通常视为超买，<30 通常视为超卖；窗口内无涨跌时取中性值 50。
    """

    def __init__(self, period: int = 14) -> None:
        """初始化 RSI 计算器。

        Args:
            period: RSI 计算周期（交易日数），默认 14。
        """
        self._period = period

    @property
    def spec(self) -> FactorSpec:
        """返回 RSI 的因子元数据。"""
        return FactorSpec(
            factor_id=f"rsi_{self._period}d",
            name=f"{self._period}日RSI",
            category="technical",
            version="1.1.0",
            description=(
                f"指数近 {self._period} 个交易日的相对强弱指标（RSI，简单平均口径），"
                "用于判断超买超卖；窗口内无涨跌时为 50。"
            ),
            required_data=["index_bars"],
            lookback_days=period_lookback_days(self._period),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 RSI（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 RSI。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        result: dict[date, FactorValue] = {}
        close_dates, close_prices = _build_sorted_closes_for_code(index_code, ctx)
        for trade_date in dates:
            idx = index_on(close_dates, trade_date)
            if idx is None or idx < self._period:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": f"收盘价数据不足 {self._period + 1} 条"},
                )
                continue
            window = close_prices[idx - self._period : idx + 1]
            diffs = [current - previous for previous, current in zip(window, window[1:])]
            avg_gain = sum(diff for diff in diffs if diff > 0) / len(diffs)
            avg_loss = sum(-diff for diff in diffs if diff < 0) / len(diffs)
            if avg_gain == 0 and avg_loss == 0:
                rsi = 50.0
            elif avg_loss == 0:
                rsi = 100.0
            else:
                rsi = round(100.0 - 100.0 / (1.0 + avg_gain / avg_loss), 2)
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=rsi,
                payload={
                    "period": self._period,
                    "avg_gain": round(avg_gain, 4),
                    "avg_loss": round(avg_loss, 4),
                },
            )
        return result


# ══════════════════════════════════════════════════════════════════════
# 回撤因子（N 交易日窗口 + 水下时间）
# ══════════════════════════════════════════════════════════════════════


class DrawdownComputer:
    """N 日回撤幅度因子计算器。

    计算当前收盘价相对近 N 个交易日最高收盘价的回撤幅度（%），
    返回负数或零，如 -12.5 表示当前价格比窗口最高收盘价低 12.5%。
    payload 附带水下时间（自峰值以来经过的交易日数），适合中线策略的仓位管理。

    Attributes:
        _period: 回望交易日数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 60) -> None:
        """初始化回撤幅度计算器。

        Args:
            period: 回望交易日数，如 60/250。

        Raises:
            ValueError: period 小于 2。
        """
        if period < 2:
            raise ValueError("period 必须至少为 2")
        self._period = int(period)
        self._lookback = period_lookback_days(self._period)

    @property
    def spec(self) -> FactorSpec:
        """返回 N 日回撤幅度的因子元数据。"""
        return FactorSpec(
            factor_id=f"drawdown_{self._period}d",
            name=f"{self._period}日回撤幅度",
            category="technical",
            version="1.0.0",
            description=(
                f"当前收盘价相对近 {self._period} 个交易日最高收盘价的回撤幅度（%），"
                "返回负数或零；payload 附自峰值以来的水下交易日数。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单日回撤幅度（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的回撤幅度。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        result: dict[date, FactorValue] = {}
        close_dates, close_prices = _build_sorted_closes_for_code(index_code, ctx)
        for trade_date in dates:
            idx = index_on(close_dates, trade_date)
            if idx is None or idx < self._period - 1:
                result[trade_date] = self._missing()
                continue
            result[trade_date] = self._build_value(close_prices[idx - self._period + 1 : idx + 1])
        return result

    def _build_value(self, window: list[float]) -> FactorValue:
        """由 N 个收盘价窗口构造回撤幅度因子值。

        Args:
            window: 从旧到新的 N 个收盘价（含当日）。

        Returns:
            FactorValue，窗口最高收盘价非正时 numeric 为 None。
        """
        highest = max(window)
        if highest <= 0:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "窗口最高收盘价非正", "period": self._period},
            )
        current_close = window[-1]
        # 水下时间：自峰值以来经过的交易日数（含当日）
        underwater_days = len(window) - 1 - window.index(highest)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round((current_close - highest) / highest * 100, 2),
            payload={
                "period": self._period,
                "window_high": round(highest, 2),
                "current_close": round(current_close, 2),
                "underwater_days": underwater_days,
            },
        )

    def _missing(self) -> FactorValue:
        """构造数据不足的缺失值结果。

        Returns:
            numeric 为 None 的 FactorValue。
        """
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=None,
            payload={
                "reason": f"收盘价数据不足 {self._period} 条或当日无行情",
                "period": self._period,
            },
        )


# ══════════════════════════════════════════════════════════════════════
# 超越均值 ± 标准差天数差（Tushare days_beyond_upper_lower_* 口径）
# ══════════════════════════════════════════════════════════════════════


class DaysBeyondUpperLowerComputer:
    """N 日超越均值 ± 标准差天数差因子计算器。

    计算口径（与 Tushare 因子库 days_beyond_upper_lower_* 一致）：
    取近 N 个交易日收盘价，先用同一窗口计算均值 mean 与样本标准差 std(ddof=1)，
    再统计 close > mean + std 的天数减去 close < mean − std 的天数。

    取值范围 [−N, N]：正数表示价格更多时间运行在均值上方（偏强），
    负数表示更多时间运行在均值下方（偏弱），0 表示两侧均衡或全程在
    均值 ± 1 个标准差之内。可作为"过热/过冷"的过滤依据。

    Attributes:
        _period: 回望交易日数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 21) -> None:
        """初始化超越均值天数差计算器。

        Args:
            period: 回望交易日数，默认 21。
        """
        self._period = period
        self._lookback = period_lookback_days(period)

    @property
    def spec(self) -> FactorSpec:
        """返回超越均值 ± 标准差天数差的因子元数据。"""
        return FactorSpec(
            factor_id=f"days_beyond_upper_lower_{self._period}d",
            name=f"{self._period}日超越均值±标准差天数差",
            category="technical",
            version="1.0.0",
            description=(
                f"近 {self._period} 个交易日收盘价中高于 mean+std 的天数减去"
                "低于 mean−std 的天数（mean/std 取同一窗口，ddof=1），"
                "衡量价格偏离均值的强度与频率。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单个交易日的超越均值 ± 标准差天数差（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的超越均值 ± 标准差天数差。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _build_sorted_closes_for_code(index_code, ctx)
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            idx = index_on(close_dates, trade_date)
            if idx is None or idx < self._period - 1:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": f"收盘价数据不足 {self._period} 条或当日无行情"},
                )
                continue
            result[trade_date] = self._build_value(close_prices[idx - self._period + 1 : idx + 1])
        return result

    def _build_value(self, window: list[float]) -> FactorValue:
        """由 N 个收盘价窗口构造天数差因子值。

        Args:
            window: 从旧到新的 N 个收盘价（含当日）。

        Returns:
            FactorValue，样本不足 2 个时 numeric 为 None。
        """
        n = len(window)
        if n < 2:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "收盘价样本不足 2 条"},
            )
        mean = sum(window) / n
        variance = sum((p - mean) ** 2 for p in window) / (n - 1)
        std = math.sqrt(variance)
        upper = mean + std
        lower = mean - std
        above = sum(1 for p in window if p > upper)
        below = sum(1 for p in window if p < lower)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=float(above - below),
            payload={
                "above": above,
                "below": below,
                "window_mean": round(mean, 6),
                "window_std": round(std, 6),
                "sample_count": n,
            },
        )
