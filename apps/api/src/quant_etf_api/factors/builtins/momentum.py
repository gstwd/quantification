"""动量类因子：5 日、20 日、60 日收益率（基于指数数据）。

不复用 domain.common.bar_metrics.calc_5d_return（数据不足时返回 0.0，语义模糊），
改用内部 _calc_nd_return：数据不足时明确返回 None，区分"零涨跌"与"无数据"。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


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


class Return5dComputer:
    """5 日动量因子计算器。

    计算近 5 个交易日的指数价格涨跌幅（%），衡量短期动量。
    数据不足 5 条时返回 None（而非 0.0），保持语义准确。
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


class Return20dComputer:
    """20 日动量因子计算器。

    计算近 20 个交易日的指数价格涨跌幅（%），衡量中期动量。
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


class Return60dComputer:
    """60 日动量因子计算器。

    计算近 60 个交易日的指数价格涨跌幅（%），衡量中长期趋势。
    需要 FactorContext 提供 90 个自然日回望以覆盖 60 个交易日。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 60 日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_60d",
            name="60日收益率",
            category="momentum",
            version="2.0.0",
            description=(
                "指数近 60 个交易日的价格涨跌幅（%），衡量中长期趋势。"
            ),
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


class Return120dComputer:
    """120 日动量因子计算器（约 6 个月）。

    计算近 120 个交易日的指数价格涨跌幅（%），衡量中长期趋势。
    用于沪深300波段策略中的 6 个月动量判断。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 120 日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_120d",
            name="120日收益率",
            category="momentum",
            version="1.0.0",
            description=(
                "指数近 120 个交易日的价格涨跌幅（%），衡量中长期趋势。"
            ),
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
