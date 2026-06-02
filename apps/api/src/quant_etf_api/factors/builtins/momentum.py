"""动量类因子：5日、20日、60日收益率。

不复用 domain.common.bar_metrics.calc_5d_return_etf（数据不足时返回 0.0，语义模糊），
改用内部 _calc_nd_return：数据不足时明确返回 None，区分"零涨跌"与"无数据"。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


def _calc_nd_return(
    etf_code: str,
    trade_date: date,
    ctx: FactorContext,
    n: int,
) -> float | None:
    """计算 ETF 近 n 个交易日收益率（%）。

    Args:
        etf_code: ETF 代码。
        trade_date: 目标交易日。
        ctx: FactorContext。
        n: 回望交易日数，需要历史数据中至少有 n 条 trade_date 之前的记录。

    Returns:
        收益率（%），历史数据不足 n 条时返回 None。
    """
    today_bar = ctx.etf_bars.get((etf_code, trade_date))
    if today_bar is None or today_bar.close_price is None:
        return None
    past_closes = sorted(
        [
            (dt, v.close_price)
            for (code, dt), v in ctx.etf_bars.items()
            if code == etf_code and dt < trade_date and v.close_price is not None
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
    """5日动量因子计算器。

    计算近5个交易日的价格涨跌幅（%），衡量短期动量。
    数据不足5条时返回 None（而非 0.0），保持语义准确。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回5日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_5d",
            name="5日收益率",
            category="momentum",
            version="1.0.0",
            description="ETF 近5个交易日的价格涨跌幅（%），衡量短期动量。",
            required_data=["etf_bars"],
        )

    def compute(self, etf_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算5日收益率。

        Args:
            etf_code: ETF 代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，历史数据不足5条时 numeric 为 None。
        """
        value = _calc_nd_return(etf_code, trade_date, ctx, n=5)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=value,
            payload={"lookback_days": 5},
        )


class Return20dComputer:
    """20日动量因子计算器。

    计算近20个交易日的价格涨跌幅（%），衡量中期动量。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回20日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_20d",
            name="20日收益率",
            category="momentum",
            version="1.0.0",
            description="ETF 近20个交易日的价格涨跌幅（%），衡量中期动量。",
            required_data=["etf_bars"],
        )

    def compute(self, etf_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算20日收益率。

        Args:
            etf_code: ETF 代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，历史数据不足20条时 numeric 为 None。
        """
        value = _calc_nd_return(etf_code, trade_date, ctx, n=20)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=value,
            payload={"lookback_days": 20},
        )


class Return60dComputer:
    """60日动量因子计算器。

    计算近60个交易日的价格涨跌幅（%），衡量中长期趋势。
    需要 FactorContext 提供 90 个自然日回望以覆盖 60 个交易日。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回60日收益率的因子元数据。"""
        return FactorSpec(
            factor_id="return_60d",
            name="60日收益率",
            category="momentum",
            version="1.0.0",
            description=(
                "ETF 近60个交易日的价格涨跌幅（%），衡量中长期趋势。"
                "需 FactorContext 提供 90 天自然日回望。"
            ),
            required_data=["etf_bars"],
        )

    def compute(self, etf_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算60日收益率。

        Args:
            etf_code: ETF 代码。
            trade_date: 目标交易日。
            ctx: FactorContext，需包含至少61条历史收盘价。

        Returns:
            FactorValue，历史数据不足60条时 numeric 为 None。
        """
        value = _calc_nd_return(etf_code, trade_date, ctx, n=60)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=value,
            payload={"lookback_days": 60},
        )
