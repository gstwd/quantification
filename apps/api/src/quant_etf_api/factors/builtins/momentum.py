"""动量类因子：收益率、低振幅条件动量与风险调整动量（基于指数数据）。

不复用 domain.common.bar_metrics.calc_5d_return（数据不足时返回 0.0，语义模糊），
改用内部 _calc_nd_return：数据不足时明确返回 None，区分"零涨跌"与"无数据"。

另含三个 Tushare 因子库口径的动量因子：
- RsrsComputer：RSRS 阻力支撑相对强度（高低价 OLS 斜率 × R² 的滚动标准分）；
- PricePositionIrComputer：日内位置比率的信息比率（(close-open)/(high-low)）；
- DaysDownUpComputer：连续涨跌天数差。

批量接口：所有计算器同时实现 BatchFactorComputer 协议，回测预计算时一次
遍历全量 bar 数据即可覆盖所有交易日，避免逐日重复构建收盘价序列。
"""

from __future__ import annotations

import bisect
import math
from collections import deque
from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue

# A 股全年约 252 个交易日，年化因子为 sqrt(252)，与 volatility.py 口径一致
_ANNUALIZE_FACTOR = math.sqrt(252)

# RSRS 默认口径：N=18 日 OLS 回归窗口，M=250 日修正斜率标准化窗口
_RSRS_N = 18
_RSRS_M = 250
# n + m − 1 = 267 个交易日 ≈ 400 自然日，额外留出缓冲
_RSRS_LOOKBACK_DAYS = 400

# 日内位置信息比率的最小有效样本数
_POSITION_IR_MIN_SAMPLES = 20

# 开源证券《A股市场中如何构造动量因子？》的默认研究口径：
# 最近 160 个交易日中，保留日振幅最低的 70% 交易日的收益率之和。
_LOW_AMPLITUDE_MOMENTUM_PERIOD = 160
_LOW_AMPLITUDE_MOMENTUM_RATIO = 0.70


def _calc_nd_return(
    index_code: str,
    trade_date: date,
    ctx: FactorContext,
    n: int,
) -> float | None:
    """计算指数近 n 个交易日收益率（%）。

    Args:
        index_code: 指数代码。
        trade_date: 目标交易日。
        ctx: FactorContext。
        n: 回望交易日数，需要历史数据中至少有 n 条 trade_date 之前的记录。

    Returns:
        收益率（%），历史数据不足 n 条时返回 None。
    """
    today_bar = ctx.index_bars.get((index_code, trade_date))
    if today_bar is None or today_bar.close_price is None:
        return None
    past_closes = sorted(
        [
            (dt, v.close_price)
            for (code, dt), v in ctx.index_bars.items()
            if code == index_code and dt < trade_date and v.close_price is not None
        ],
        key=lambda x: x[0],
    )
    if len(past_closes) < n:
        return None
    base_close = past_closes[-n][1]
    if base_close <= 0:
        return None
    return round((today_bar.close_price / base_close - 1) * 100, 4)


def _build_sorted_closes(
    index_code: str,
    ctx: FactorContext,
) -> tuple[list[date], list[float]]:
    """从 FactorContext 提取指定指数的有效收盘价序列（升序）。

    Args:
        index_code: 指数代码。
        ctx: FactorContext。

    Returns:
        (close_dates, close_prices) 元组，已按日期升序排列。
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


def _calc_batch_returns(
    close_dates: list[date],
    close_prices: list[float],
    dates: list[date],
    n: int,
    factor_id: str,
) -> dict[date, FactorValue]:
    """批量计算 n 日收益率，复用已排序的收盘价序列。

    Args:
        close_dates: 已排序的收盘价日期列表。
        close_prices: 对应收盘价列表。
        dates: 需要计算的交易日列表。
        n: 回望交易日数。
        factor_id: 因子标识，用于构建 FactorValue。

    Returns:
        key=交易日, value=FactorValue 的字典。
    """
    result: dict[date, FactorValue] = {}
    if not close_dates:
        return {d: FactorValue(factor_id=factor_id, numeric=None) for d in dates}

    for trade_date in dates:
        # 找 trade_date 在有序列表中的位置
        idx = bisect.bisect_right(close_dates, trade_date) - 1
        if idx < 0 or close_dates[idx] != trade_date or idx < n:
            result[trade_date] = FactorValue(factor_id=factor_id, numeric=None)
            continue
        base_price = close_prices[idx - n]
        current_price = close_prices[idx]
        if base_price <= 0:
            result[trade_date] = FactorValue(factor_id=factor_id, numeric=None)
            continue
        value = round((current_price / base_price - 1) * 100, 4)
        result[trade_date] = FactorValue(
            factor_id=factor_id,
            numeric=value,
            payload={"lookback_days": n},
        )
    return result


def _calc_nd_annualized_vol(
    close_dates: list[date],
    close_prices: list[float],
    trade_date: date,
    n: int = 20,
) -> float | None:
    """计算截至 trade_date 的 n 日年化波动率（%），复用已排序收盘价序列。

    与 volatility.py 的 Volatility20dComputer 使用相同口径：
    std(近 n 个日收益率, ddof=1) × sqrt(252) × 100，需要 n+1 个连续收盘价。

    Args:
        close_dates: 已排序的收盘价日期列表。
        close_prices: 对应收盘价列表。
        trade_date: 目标交易日。
        n: 日收益率数量（默认 20）。

    Returns:
        年化波动率（%），数据不足时返回 None。
    """
    idx = bisect.bisect_right(close_dates, trade_date) - 1
    if idx < 0 or close_dates[idx] != trade_date or idx < n:
        return None
    recent = close_prices[idx - n : idx + 1]
    daily_returns = [
        (recent[i] - recent[i - 1]) / recent[i - 1]
        for i in range(1, len(recent))
        if recent[i - 1] > 0
    ]
    if len(daily_returns) < 2:
        return None
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    return round(math.sqrt(variance) * _ANNUALIZE_FACTOR * 100, 4)


class Return5dComputer:
    """5 日动量因子计算器。

    计算近 5 个交易日的指数价格涨跌幅（%），衡量短期动量。
    数据不足 5 条时返回 None（而非 0.0），保持语义准确。
    实现 BatchFactorComputer 协议，支持回测批量预计算。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 5 日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_5d",
            name="5日收益率",
            category="momentum",
            version="2.0.0",
            description="指数近 5 个交易日的价格涨跌幅（%），衡量短期动量。",
            required_data=["index_bars"],
            lookback_days=15,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 5 日收益率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，历史数据不足 5 条时 numeric 为 None。
        """
        value = _calc_nd_return(index_code, trade_date, ctx, n=5)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=value,
            payload={"lookback_days": 5},
        )

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 5 日收益率。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _build_sorted_closes(index_code, ctx)
        return _calc_batch_returns(
            close_dates, close_prices, dates, n=5, factor_id=self.spec.factor_id
        )


class Return20dComputer:
    """20 日动量因子计算器。

    计算近 20 个交易日的指数价格涨跌幅（%），衡量中期动量。
    实现 BatchFactorComputer 协议，支持回测批量预计算。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 20 日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_20d",
            name="20日收益率",
            category="momentum",
            version="2.0.0",
            description="指数近 20 个交易日的价格涨跌幅（%），衡量中期动量。",
            required_data=["index_bars"],
            lookback_days=40,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 20 日收益率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，历史数据不足 20 条时 numeric 为 None。
        """
        value = _calc_nd_return(index_code, trade_date, ctx, n=20)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=value,
            payload={"lookback_days": 20},
        )

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 20 日收益率。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _build_sorted_closes(index_code, ctx)
        return _calc_batch_returns(
            close_dates, close_prices, dates, n=20, factor_id=self.spec.factor_id
        )


class Return17dComputer:
    """17 日动量因子计算器。

    计算近 17 个交易日的指数价格涨跌幅（%），衡量中短期动量。
    实现 BatchFactorComputer 协议，支持回测批量预计算。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 17 日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_17d",
            name="17日收益率",
            category="momentum",
            version="1.0.0",
            description="指数近 17 个交易日的价格涨跌幅（%），衡量中短期动量。",
            required_data=["index_bars"],
            lookback_days=35,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 17 日收益率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，历史数据不足 17 条时 numeric 为 None。
        """
        value = _calc_nd_return(index_code, trade_date, ctx, n=17)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=value,
            payload={"lookback_days": 17},
        )

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 17 日收益率。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _build_sorted_closes(index_code, ctx)
        return _calc_batch_returns(
            close_dates, close_prices, dates, n=17, factor_id=self.spec.factor_id
        )


class Return60dComputer:
    """60 日动量因子计算器。

    计算近 60 个交易日的指数价格涨跌幅（%），衡量中长期趋势。
    需要 FactorContext 提供 90 个自然日回望以覆盖 60 个交易日。
    实现 BatchFactorComputer 协议，支持回测批量预计算。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 60 日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_60d",
            name="60日收益率",
            category="momentum",
            version="2.0.0",
            description=("指数近 60 个交易日的价格涨跌幅（%），衡量中长期趋势。"),
            required_data=["index_bars"],
            lookback_days=90,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 60 日收益率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext，需包含至少 61 条历史收盘价。

        Returns:
            FactorValue，历史数据不足 60 条时 numeric 为 None。
        """
        value = _calc_nd_return(index_code, trade_date, ctx, n=60)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=value,
            payload={"lookback_days": 60},
        )

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 60 日收益率。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _build_sorted_closes(index_code, ctx)
        return _calc_batch_returns(
            close_dates, close_prices, dates, n=60, factor_id=self.spec.factor_id
        )


class Return120dComputer:
    """120 日动量因子计算器（约 6 个月）。

    计算近 120 个交易日的指数价格涨跌幅（%），衡量中长期趋势。
    用于沪深300波段策略中的 6 个月动量判断。
    实现 BatchFactorComputer 协议，支持回测批量预计算。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 120 日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_120d",
            name="120日收益率",
            category="momentum",
            version="1.0.0",
            description=("指数近 120 个交易日的价格涨跌幅（%），衡量中长期趋势。"),
            required_data=["index_bars"],
            lookback_days=180,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 120 日收益率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext，需包含至少 121 条历史收盘价。

        Returns:
            FactorValue，历史数据不足 120 条时 numeric 为 None。
        """
        value = _calc_nd_return(index_code, trade_date, ctx, n=120)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=value,
            payload={"lookback_days": 120},
        )

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 120 日收益率。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _build_sorted_closes(index_code, ctx)
        return _calc_batch_returns(
            close_dates, close_prices, dates, n=120, factor_id=self.spec.factor_id
        )


class Sharpe60dComputer:
    """60 日风险调整动量因子计算器（夏普式比率）。

    计算公式：sharpe = return_60d / volatility_20d。
    return_60d 为近 60 个交易日涨跌幅（%），volatility_20d 为近 20 个
    交易日年化波动率（%），两者相除得到"每单位波动获得的中期收益"，
    用于在动量轮动中同时奖励涨幅与惩罚高波动，选"涨得稳"而非"涨得猛"。
    实现 BatchFactorComputer 协议，支持回测批量预计算。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 60 日风险调整动量的因子元数据。"""
        return FactorSpec(
            factor_id="sharpe_60d",
            name="60日风险调整动量",
            category="momentum",
            version="1.0.0",
            description=(
                "60日风险调整动量 = 近60个交易日收益率(%) / 近20个交易日年化波动率(%)。"
                "衡量每单位波动获得的中期收益，值越高表示动量越稳健。"
            ),
            required_data=["index_bars"],
            lookback_days=90,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 60 日风险调整动量。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext，需包含至少 61 条历史收盘价。

        Returns:
            FactorValue，数据不足或波动率为 0 时 numeric 为 None。
        """
        ret = _calc_nd_return(index_code, trade_date, ctx, n=60)
        closes = sorted(
            [
                (dt, v.close_price)
                for (code, dt), v in ctx.index_bars.items()
                if code == index_code and dt <= trade_date and v.close_price is not None
            ],
            key=lambda x: x[0],
        )
        close_dates = [d for d, _ in closes]
        close_prices = [p for _, p in closes]
        vol = _calc_nd_annualized_vol(close_dates, close_prices, trade_date, n=20)
        if ret is None or vol is None or vol <= 0:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"return_60d": ret, "volatility_20d": vol},
            )
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(ret / vol, 4),
            payload={"return_60d": ret, "volatility_20d": vol},
        )

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 60 日风险调整动量。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _build_sorted_closes(index_code, ctx)
        result: dict[date, FactorValue] = {}
        if not close_dates:
            return {d: FactorValue(factor_id=self.spec.factor_id, numeric=None) for d in dates}

        # 一次批量计算 60 日收益率，后续逐日只补充波动率与比值
        returns = _calc_batch_returns(
            close_dates, close_prices, dates, n=60, factor_id=self.spec.factor_id
        )
        for trade_date in dates:
            ret = returns[trade_date].numeric
            vol = _calc_nd_annualized_vol(close_dates, close_prices, trade_date, n=20)
            if ret is None or vol is None or vol <= 0:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"return_60d": ret, "volatility_20d": vol},
                )
                continue
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(ret / vol, 4),
                payload={"return_60d": ret, "volatility_20d": vol},
            )
        return result


class LowAmplitudeMomentumComputer:
    """低振幅条件动量因子计算器。

    对每个指数，在最近 N 个交易日中以日振幅 ``high / low - 1`` 排序，
    仅累加振幅最低的 λ 比例交易日的收盘价日收益率。该口径将普通涨跌幅中
    更容易伴随过度反应的高振幅日剔除，保留相对平稳日的趋势收益。

    默认参数 N=160、λ=70% 来自开源证券《A股市场中如何构造动量因子？》；
    这是基于指数 OHLC 的资产级适配版，不等同于原研究的个股选股因子。
    """

    def __init__(
        self,
        period: int = _LOW_AMPLITUDE_MOMENTUM_PERIOD,
        low_amplitude_ratio: float = _LOW_AMPLITUDE_MOMENTUM_RATIO,
    ) -> None:
        if period < 2:
            raise ValueError("period 必须至少为 2 个交易日")
        if not 0 < low_amplitude_ratio <= 1:
            raise ValueError("low_amplitude_ratio 必须在 (0, 1] 内")
        self._period = period
        self._ratio = low_amplitude_ratio
        self._selected_days = max(1, round(period * low_amplitude_ratio))

    @property
    def spec(self) -> FactorSpec:
        ratio_pct = int(self._ratio * 100)
        return FactorSpec(
            factor_id=f"low_amplitude_momentum_{self._period}d_{ratio_pct}pct",
            name=f"{self._period}日低振幅动量（{ratio_pct}%）",
            category="momentum",
            version="1.0.0",
            description=(
                f"最近 {self._period} 个交易日按日振幅(high/low−1)从低到高排序，"
                f"累加最低振幅 {ratio_pct}% 交易日的收盘价日收益率（%）。"
            ),
            required_data=["index_bars"],
            lookback_days=max(15, int(self._period * 1.5) + 10),
            default_params={
                "period": self._period,
                "low_amplitude_ratio": self._ratio,
            },
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单个交易日的低振幅条件动量。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算低振幅条件动量，逐日口径与 ``compute`` 完全一致。"""
        bars = sorted(
            [
                (dt, v.high_price, v.low_price, v.close_price)
                for (code, dt), v in ctx.index_bars.items()
                if code == index_code
                and v.high_price is not None
                and v.low_price is not None
                and v.close_price is not None
                and v.low_price > 0
            ],
            key=lambda x: x[0],
        )
        bar_dates = [bar[0] for bar in bars]
        result: dict[date, FactorValue] = {}

        for trade_date in dates:
            idx = bisect.bisect_right(bar_dates, trade_date) - 1
            # N 个日收益率需要 N+1 个连续有效收盘价；首日只作收益率基准。
            if idx < self._period or idx < 0 or bar_dates[idx] != trade_date:
                result[trade_date] = self._missing("OHLC 数据不足或当日无完整行情")
                continue

            window = bars[idx - self._period : idx + 1]
            candidates: list[tuple[float, float]] = []
            valid = True
            for previous, current in zip(window, window[1:]):
                _, _, _, previous_close = previous
                _, high, low, close = current
                if previous_close <= 0 or low <= 0 or high < low:
                    valid = False
                    break
                amplitude = high / low - 1
                daily_return = (close / previous_close - 1) * 100
                candidates.append((amplitude, daily_return))
            if not valid or len(candidates) != self._period:
                result[trade_date] = self._missing("OHLC 数据存在无效价格")
                continue

            selected = sorted(candidates, key=lambda item: item[0])[: self._selected_days]
            value = sum(daily_return for _, daily_return in selected)
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(value, 4),
                payload={
                    "period": self._period,
                    "low_amplitude_ratio": self._ratio,
                    "selected_days": len(selected),
                    "amplitude_cutoff": round(selected[-1][0] * 100, 4),
                    "selected_return_sum": round(value, 4),
                },
            )
        return result

    def _missing(self, reason: str) -> FactorValue:
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=None,
            payload={
                "reason": reason,
                "period": self._period,
                "low_amplitude_ratio": self._ratio,
            },
        )


# ══════════════════════════════════════════════════════════════════════
# Tushare 因子库口径的动量因子
# ══════════════════════════════════════════════════════════════════════


def _build_sorted_high_low(
    index_code: str,
    ctx: FactorContext,
) -> tuple[list[date], list[float], list[float]]:
    """提取指定指数的有序（最高价、最低价）序列，供 RSRS 批量计算复用。

    仅保留最高价与最低价均非空的行，避免把缺失行当成零值参与回归。

    Args:
        index_code: 指数代码。
        ctx: FactorContext，包含全量回望数据。

    Returns:
        (dates, lows, highs) 三元组，均按日期升序排列。
    """
    bars = sorted(
        [
            (dt, v.low_price, v.high_price)
            for (code, dt), v in ctx.index_bars.items()
            if code == index_code
            and v.low_price is not None
            and v.high_price is not None
        ],
        key=lambda x: x[0],
    )
    if not bars:
        return [], [], []
    return [d for d, _, _ in bars], [lo for _, lo, _ in bars], [hi for _, _, hi in bars]


def _build_sorted_ohlc(
    index_code: str,
    ctx: FactorContext,
) -> list[tuple[date, float, float, float, float]]:
    """提取指定指数的有序 OHLC 序列，供日内位置因子批量计算复用。

    仅保留开高低收均非空的行。

    Args:
        index_code: 指数代码。
        ctx: FactorContext，包含全量回望数据。

    Returns:
        [(date, open, high, low, close), ...] 列表，按日期升序排列。
    """
    return sorted(
        [
            (dt, v.open_price, v.high_price, v.low_price, v.close_price)
            for (code, dt), v in ctx.index_bars.items()
            if code == index_code
            and v.close_price is not None
            and v.high_price is not None
            and v.low_price is not None
            and v.open_price is not None
        ],
        key=lambda x: x[0],
    )


def _ols_slope_r2(
    xs: list[float],
    ys: list[float],
) -> tuple[float, float] | None:
    """对 (x, y) 做最小二乘线性回归，返回斜率与决定系数。

    Args:
        xs: 自变量序列（RSRS 中为最低价）。
        ys: 因变量序列（RSRS 中为最高价）。
        len(xs) 需与 len(ys) 一致且不少于 2。

    Returns:
        (斜率 β, 决定系数 R²)；样本不足、自变量无变异或回归退化时返回 None。
    """
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    beta = sxy / sxx
    alpha = mean_y - beta * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot <= 0:
        return beta, 0.0
    ss_res = sum((y - (alpha + beta * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot
    return beta, max(0.0, min(1.0, r2))


class RsrsComputer:
    """RSRS 阻力支撑相对强度因子计算器。

    计算口径：
    1. 近 N=18 个交易日，以最低价为自变量、最高价为因变量做 OLS 回归，
       取斜率 β，并计算修正值 β_raw = β × R²（降低拟合质量差的斜率权重）；
    2. 对最近 M=250 个交易日的 β_raw 序列做 z-score 标准化，
       输出标准分 (β_raw − mean) / std（ddof=1）。

    标准分为无量纲数值，正数代表支撑强于阻力（趋势偏多），
    配合 rsrs_score 变换映射到 0-100 择时得分空间使用。
    实现 BatchFactorComputer 协议，回测预计算时一次遍历全量 bar 数据。

    Attributes:
        _n: OLS 回归窗口（交易日）。
        _m: β 修正值标准化窗口（交易日）。
    """

    def __init__(self, n: int = _RSRS_N, m: int = _RSRS_M) -> None:
        """初始化 RSRS 计算器。

        Args:
            n: OLS 回归窗口（交易日），默认 18。
            m: 标准化窗口（交易日），默认 250。
        """
        self._n = n
        self._m = m

    @property
    def spec(self) -> FactorSpec:
        """返回 RSRS 的因子元数据。"""
        return FactorSpec(
            factor_id="rsrs",
            name="RSRS阻力支撑相对强度",
            category="momentum",
            version="1.0.0",
            description=(
                f"RSRS 阻力支撑相对强度：近 {self._n} 个交易日最高价对最低价做 OLS 回归，"
                f"取斜率 β × R² 为修正值，再对最近 {self._m} 个交易日的修正值序列做 "
                "z-score 标准化，输出标准分。正数代表支撑强于阻力。"
            ),
            required_data=["index_bars"],
            lookback_days=_RSRS_LOOKBACK_DAYS,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单个交易日的 RSRS 标准分。

        与 compute_batch 共用同一实现，保证逐点与批量结果完全一致。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext，需含 high_price / low_price。

        Returns:
            FactorValue，数据不足或标准化窗口内标准差为 0 时 numeric 为 None。
        """
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 RSRS 标准分。

        先用一次 O(N) 遍历算出全部修正斜率序列，再用长度为 M 的滚动窗口
        计算均值与标准差（每 M 次重新累计一次以抑制浮点漂移），
        整体复杂度 O(bar 数 × N)，避免逐日重复回归。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        n = self._n
        m = self._m
        bars_dates, lows, highs = _build_sorted_high_low(index_code, ctx)
        empty = {d: self._missing("缺少有效最高价/最低价数据") for d in dates}
        if not bars_dates:
            return empty
        if len(bars_dates) < n + m - 1:
            for trade_date in dates:
                empty[trade_date] = self._missing(
                    f"高低价数据不足 {n + m - 1} 条或当日无行情"
                )
            return empty

        # 修正斜率序列：索引与 bars_dates 对齐，前 n−1 个位置无值
        adjusted: list[float | None] = [None] * len(bars_dates)
        for i in range(n - 1, len(bars_dates)):
            pair = _ols_slope_r2(lows[i - n + 1 : i + 1], highs[i - n + 1 : i + 1])
            adjusted[i] = None if pair is None else pair[0] * pair[1]

        # 滚动 z-score：窗口内出现空值即清空窗口，不跨越数据缺口
        window: deque[float] = deque()
        total = 0.0
        total_sq = 0.0
        computed: dict[date, FactorValue] = {}
        updates = 0
        for i in range(n - 1, len(bars_dates)):
            current = adjusted[i]
            if current is None:
                window.clear()
                total = 0.0
                total_sq = 0.0
                updates = 0
                continue
            window.append(current)
            total += current
            total_sq += current * current
            updates += 1
            if len(window) > m:
                dropped = window.popleft()
                total -= dropped
                total_sq -= dropped * dropped
            if len(window) < m:
                continue
            if updates % m == 0:
                # 周期性重新累计，消除长序列浮点累加漂移
                total = sum(window)
                total_sq = sum(v * v for v in window)
            mean = total / m
            variance = (total_sq - total * total / m) / (m - 1)
            if variance <= 1e-12:
                computed[bars_dates[i]] = self._missing("标准化窗口内斜率无变异")
                continue
            std = math.sqrt(variance)
            computed[bars_dates[i]] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round((current - mean) / std, 4),
                payload={
                    "adjusted_beta": round(current, 6),
                    "mean": round(mean, 6),
                    "std": round(std, 6),
                    "n": n,
                    "m": m,
                },
            )

        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            result[trade_date] = computed.get(
                trade_date,
                self._missing(f"高低价数据不足 {n + m - 1} 条或当日无行情"),
            )
        return result

    def _missing(self, reason: str) -> FactorValue:
        """构造缺失值结果。

        Args:
            reason: 缺失原因说明。

        Returns:
            numeric 为 None 的 FactorValue。
        """
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=None,
            payload={"reason": reason, "n": self._n, "m": self._m},
        )


class PricePositionIrComputer:
    """日内位置信息比率因子计算器。

    计算口径：近 N 个交易日日内位置比率 r = (close − open) / (high − low)，
    输出 mean(r) / std(r, ddof=1)。r 衡量收盘价在当日区间中的相对位置，
    取信息比率后同时刻画买盘强度与稳定性；high == low 的交易日跳过。
    有效样本不足 20 个或标准差为 0 时返回 None。
    实现 BatchFactorComputer 协议，支持回测批量预计算。

    Attributes:
        _period: 回望交易日数。
    """

    def __init__(self, period: int = 60) -> None:
        """初始化日内位置信息比率计算器。

        Args:
            period: 回望交易日数，默认 60。
        """
        self._period = period

    @property
    def spec(self) -> FactorSpec:
        """返回日内位置信息比率的因子元数据。"""
        return FactorSpec(
            factor_id=f"price_position_ir_{self._period}d",
            name=f"{self._period}日日内位置信息比率",
            category="momentum",
            version="1.0.0",
            description=(
                f"指数近 {self._period} 个交易日日内位置比率 "
                "(close − open) / (high − low) 的均值与标准差之比（信息比率），"
                "衡量收盘价位于当日区间上沿的强度与稳定性。"
            ),
            required_data=["index_bars"],
            lookback_days=max(15, int(self._period * 1.5) + 5),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单个交易日的日内位置信息比率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext，需含 open/high/low/close。

        Returns:
            FactorValue，数据不足或标准差为 0 时 numeric 为 None。
        """
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的日内位置信息比率。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        bars = _build_sorted_ohlc(index_code, ctx)
        bar_dates = [b[0] for b in bars]
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            idx = bisect.bisect_right(bar_dates, trade_date) - 1
            if idx < 0 or bar_dates[idx] != trade_date or idx < self._period - 1:
                result[trade_date] = self._missing(
                    f"OHLC 数据不足 {self._period} 条或当日无完整行情"
                )
                continue
            window = bars[idx - self._period + 1 : idx + 1]
            ratios = [
                (close - open_price) / (high - low)
                for _, open_price, high, low, close in window
                if high > low
            ]
            if len(ratios) < _POSITION_IR_MIN_SAMPLES:
                result[trade_date] = self._missing(
                    f"有效日内位置样本不足 {_POSITION_IR_MIN_SAMPLES} 个"
                )
                continue
            mean = sum(ratios) / len(ratios)
            variance = sum((r - mean) ** 2 for r in ratios) / (len(ratios) - 1)
            if variance <= 1e-12:
                result[trade_date] = self._missing("日内位置比率标准差为 0")
                continue
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(mean / math.sqrt(variance), 4),
                payload={
                    "mean_ratio": round(mean, 6),
                    "std_ratio": round(math.sqrt(variance), 6),
                    "sample_count": len(ratios),
                },
            )
        return result

    def _missing(self, reason: str) -> FactorValue:
        """构造缺失值结果。

        Args:
            reason: 缺失原因说明。

        Returns:
            numeric 为 None 的 FactorValue。
        """
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=None,
            payload={"reason": reason, "period": self._period},
        )


class DaysDownUpComputer:
    """连续涨跌天数差因子计算器。

    计算口径（与 Tushare 因子库 days_down_up 一致）：
    |连续上涨天数 − 连续下跌天数| − 1。
    上涨定义为当日收盘价高于前一日，下跌定义为低于前一日；
    平盘不延续任何一侧，因此平盘当日的取值为 −1。
    实现 BatchFactorComputer 协议，支持回测批量预计算。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回连续涨跌天数差的因子元数据。"""
        return FactorSpec(
            factor_id="days_down_up",
            name="连续涨跌天数差",
            category="momentum",
            version="1.0.0",
            description=(
                "连续上涨天数与连续下跌天数之差的绝对值减 1："
                "|ConsecutiveUpDays − ConsecutiveDownDays| − 1，"
                "衡量趋势的持续性；平盘不延续连涨/连跌计数。"
            ),
            required_data=["index_bars"],
            lookback_days=120,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单个交易日的连续涨跌天数差。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，至少需要 2 个收盘价，不足时 numeric 为 None。
        """
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的连续涨跌天数差。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        close_dates, close_prices = _build_sorted_closes(index_code, ctx)
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            idx = bisect.bisect_right(close_dates, trade_date) - 1
            if idx < 1 or close_dates[idx] != trade_date:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": "收盘价数据不足 2 条或当日无行情"},
                )
                continue
            up_days = 0
            while idx - up_days - 1 >= 0 and (
                close_prices[idx - up_days] > close_prices[idx - up_days - 1]
            ):
                up_days += 1
            down_days = 0
            while idx - down_days - 1 >= 0 and (
                close_prices[idx - down_days] < close_prices[idx - down_days - 1]
            ):
                down_days += 1
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=float(abs(up_days - down_days) - 1),
                payload={"up_days": up_days, "down_days": down_days},
            )
        return result
