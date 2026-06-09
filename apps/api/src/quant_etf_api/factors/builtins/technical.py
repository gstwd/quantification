"""技术指标类因子：均线、ATR、Donchian 通道、RSI（基于指数数据）。

补充趋势类和通道类因子的缺失，支持双均线、海龟交易、RSI 超买超卖等经典策略。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


def _get_historical_closes(
    index_code: str, trade_date: date, ctx: FactorContext, n: int
) -> list[float] | None:
    """获取指数近 n 个交易日的收盘价序列（含当日），升序排列。

    Args:
        index_code: 指数代码。
        trade_date: 目标交易日。
        ctx: FactorContext。
        n: 需要的收盘价数量。

    Returns:
        收盘价列表（从旧到新），数据不足时返回 None。
    """
    today_bar = ctx.index_bars.get((index_code, trade_date))
    if today_bar is None or today_bar.close_price is None:
        return None
    past_closes = sorted(
        [
            (dt, v.close_price)
            for (code, dt), v in ctx.index_bars.items()
            if code == index_code and dt <= trade_date and v.close_price is not None
        ],
        key=lambda x: x[0],
    )
    if len(past_closes) < n:
        return None
    return [c for _, c in past_closes[-n:]]


def _get_historical_bars(
    index_code: str, trade_date: date, ctx: FactorContext, n: int
) -> list[tuple[date, float, float, float, float]] | None:
    """获取指数近 n 个交易日的 OHLC 数据（含当日），升序排列。

    Args:
        index_code: 指数代码。
        trade_date: 目标交易日。
        ctx: FactorContext。
        n: 需要的日线数量。

    Returns:
        [(date, open, high, low, close), ...] 列表，数据不足时返回 None。
    """
    today_bar = ctx.index_bars.get((index_code, trade_date))
    if today_bar is None or today_bar.close_price is None:
        return None
    past = sorted(
        [
            (dt, v.open_price, v.high_price, v.low_price, v.close_price)
            for (code, dt), v in ctx.index_bars.items()
            if code == index_code
            and dt <= trade_date
            and v.close_price is not None
        ],
        key=lambda x: x[0],
    )
    if len(past) < n:
        return None
    return past[-n:]


# ══════════════════════════════════════════════════════════════════════
# 均线因子
# ══════════════════════════════════════════════════════════════════════


class MAComputer:
    """移动均线因子计算器。

    计算指定周期的简单移动平均（SMA）。
    通过构造函数参数 period 生成不同周期的实例（MA5/MA10/MA20/MA60）。

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
        # 自然日 ≈ 交易日 × 1.5 加安全余量
        self._lookback = max(15, int(period * 1.5) + 5)

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
        """计算移动均线。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        closes = _get_historical_closes(index_code, trade_date, ctx, self._period)
        if closes is None:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"reason": f"收盘价数据不足 {self._period} 条"},
            )
        ma = round(sum(closes) / len(closes), 4)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=ma,
            payload={"period": self._period, "sample_count": len(closes)},
        )


# ══════════════════════════════════════════════════════════════════════
# ATR 因子（Average True Range）
# ══════════════════════════════════════════════════════════════════════


class ATRComputer:
    """平均真实波幅（ATR）因子计算器。

    默认 14 日 ATR，用于衡量市场波动程度，
    是海龟交易法则中仓位管理和止损设置的核心参数。

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
            lookback_days=max(15, int(self._period * 1.5) + 5),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 ATR。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        # 需要 period+1 条数据以计算 period 个 True Range
        bars = _get_historical_bars(index_code, trade_date, ctx, self._period + 1)
        if bars is None:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"reason": f"日线数据不足 {self._period + 1} 条"},
            )

        tr_list: list[float] = []
        for i in range(1, len(bars)):
            _, _o, high, low, _c = bars[i]
            _, _po, _ph, _pl, prev_c = bars[i - 1]
            tr = max(high - low, abs(high - prev_c), abs(low - prev_c))
            tr_list.append(tr)

        if not tr_list:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"reason": "无法计算 True Range"},
            )

        atr = round(sum(tr_list) / len(tr_list), 4)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=atr,
            payload={"period": self._period, "tr_count": len(tr_list)},
        )


# ══════════════════════════════════════════════════════════════════════
# Donchian 通道因子
# ══════════════════════════════════════════════════════════════════════


class DonchianHighComputer:
    """Donchian 通道上轨因子计算器。

    计算过去 N 个交易日的最高价，用于海龟交易法则的突破买入信号。
    """

    def __init__(self, period: int = 20) -> None:
        """初始化 Donchian 通道上轨计算器。

        Args:
            period: 回望周期（交易日数），默认 20。
        """
        self._period = period

    @property
    def spec(self) -> FactorSpec:
        """返回 Donchian 通道上轨的因子元数据。"""
        return FactorSpec(
            factor_id=f"donchian_{self._period}d_high",
            name=f"{self._period}日通道上轨",
            category="technical",
            version="1.0.0",
            description=f"指数近 {self._period} 个交易日的最高价，用于突破策略。",
            required_data=["index_bars"],
            lookback_days=max(15, int(self._period * 1.5) + 5),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 Donchian 通道上轨。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        bars = _get_historical_bars(index_code, trade_date, ctx, self._period)
        if bars is None:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"reason": f"日线数据不足 {self._period} 条"},
            )
        highest = max(b[2] for b in bars if b[2] is not None)  # high price
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=highest,
            payload={"period": self._period},
        )


class DonchianLowComputer:
    """Donchian 通道下轨因子计算器。

    计算过去 N 个交易日的最低价，用于海龟交易法则的跌破止损信号。
    """

    def __init__(self, period: int = 20) -> None:
        """初始化 Donchian 通道下轨计算器。

        Args:
            period: 回望周期（交易日数），默认 20。
        """
        self._period = period

    @property
    def spec(self) -> FactorSpec:
        """返回 Donchian 通道下轨的因子元数据。"""
        return FactorSpec(
            factor_id=f"donchian_{self._period}d_low",
            name=f"{self._period}日通道下轨",
            category="technical",
            version="1.0.0",
            description=f"指数近 {self._period} 个交易日的最低价，用于止损策略。",
            required_data=["index_bars"],
            lookback_days=max(15, int(self._period * 1.5) + 5),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 Donchian 通道下轨。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        bars = _get_historical_bars(index_code, trade_date, ctx, self._period)
        if bars is None:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"reason": f"日线数据不足 {self._period} 条"},
            )
        lowest = min(b[3] for b in bars if b[3] is not None)  # low price
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=lowest,
            payload={"period": self._period},
        )


# ══════════════════════════════════════════════════════════════════════
# RSI 因子（Relative Strength Index）
# ══════════════════════════════════════════════════════════════════════


class RSIComputer:
    """相对强弱指标（RSI）因子计算器。

    默认 14 日 RSI，Wilder 平滑方式。
    RSI = 100 - 100 / (1 + RS)，其中 RS = 平均涨幅 / 平均跌幅。
    取值 0-100，>70 通常视为超买，<30 通常视为超卖。
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
            version="1.0.0",
            description=(
                f"指数近 {self._period} 个交易日的相对强弱指标（RSI），"
                "用于判断超买超卖。"
            ),
            required_data=["index_bars"],
            lookback_days=max(15, int(self._period * 1.5) + 5),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 RSI。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        # 需要 period+1 条数据以计算 period 个涨跌幅
        closes = _get_historical_closes(index_code, trade_date, ctx, self._period + 1)
        if closes is None:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"reason": f"收盘价数据不足 {self._period + 1} 条"},
            )

        gains: list[float] = []
        losses: list[float] = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))

        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = round(100.0 - 100.0 / (1.0 + rs), 2)

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=rsi,
            payload={"period": self._period, "avg_gain": round(avg_gain, 4), "avg_loss": round(avg_loss, 4)},
        )


# ══════════════════════════════════════════════════════════════════════
# 最大回撤因子（60 日）
# ══════════════════════════════════════════════════════════════════════


class MaxDrawdown60dComputer:
    """60 日最大回撤因子计算器。

    计算当前收盘价相对近 60 个交易日最高价的回撤幅度（%）。
    返回值为负数或零，如 -12.5 表示当前价格比 60 日最高价低 12.5%。
    用于沪深300波段策略中的回调幅度判断。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 60 日最大回撤的因子元数据。"""
        return FactorSpec(
            factor_id="max_drawdown_60d",
            name="60日回撤幅度",
            category="technical",
            version="1.0.0",
            description=(
                "当前收盘价相对近 60 个交易日最高价的回撤幅度（%），"
                "返回负数或零，如 -12.5 表示回撤 12.5%。"
            ),
            required_data=["index_bars"],
            lookback_days=90,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 60 日回撤幅度。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        closes = _get_historical_closes(index_code, trade_date, ctx, 60)
        if closes is None:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"reason": "收盘价数据不足 60 条"},
            )
        current_close = closes[-1]
        highest = max(closes)
        if highest <= 0:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"reason": "最高价异常"},
            )
        drawdown = round((current_close - highest) / highest * 100, 2)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=drawdown,
            payload={"highest_60d": round(highest, 2), "current_close": round(current_close, 2)},
        )
