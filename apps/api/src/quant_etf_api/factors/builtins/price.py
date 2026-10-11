"""价格类因子：收盘价、涨跌幅（基于指数数据）。

这两个因子直接从 index_daily_bar 读取，无需复杂计算；批量接口逐日查表。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import (
    USAGE_FILTER,
    USAGE_TIMING,
    FactorContext,
    FactorSpec,
    FactorValue,
)


class ClosePriceComputer:
    """当日收盘价因子。"""

    @property
    def spec(self) -> FactorSpec:
        return FactorSpec(
            factor_id="close_price",
            name="收盘价",
            category="price",
            version="1.0.0",
            description="当日收盘价，直接从日线数据读取（绝对点位，只用于择时/过滤比较）",
            required_data=["index_bars"],
            lookback_days=1,
            usage=[USAGE_TIMING, USAGE_FILTER],
        )

    def compute(
        self,
        index_code: str,
        trade_date: date,
        ctx: FactorContext,
    ) -> FactorValue:
        """返回当日收盘价。"""
        bar = ctx.index_bars.get((index_code, trade_date))
        if bar is None or bar.close_price is None:
            return FactorValue(factor_id="close_price", numeric=None)
        return FactorValue(factor_id="close_price", numeric=float(bar.close_price))

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量读取各交易日取值（逐日 O(1) 查表，仅读当日行情）。"""
        return {trade_date: self.compute(index_code, trade_date, ctx) for trade_date in dates}


class ChangePctComputer:
    """当日涨跌幅因子。"""

    @property
    def spec(self) -> FactorSpec:
        return FactorSpec(
            factor_id="change_pct",
            name="涨跌幅",
            category="price",
            version="1.0.0",
            description="当日涨跌幅（%），直接从日线数据读取",
            required_data=["index_bars"],
            lookback_days=1,
        )

    def compute(
        self,
        index_code: str,
        trade_date: date,
        ctx: FactorContext,
    ) -> FactorValue:
        """返回当日涨跌幅（%）。"""
        bar = ctx.index_bars.get((index_code, trade_date))
        if bar is None or bar.change_pct is None:
            return FactorValue(factor_id="change_pct", numeric=None)
        return FactorValue(factor_id="change_pct", numeric=float(bar.change_pct))

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量读取各交易日取值（逐日 O(1) 查表，仅读当日行情）。"""
        return {trade_date: self.compute(index_code, trade_date, ctx) for trade_date in dates}
