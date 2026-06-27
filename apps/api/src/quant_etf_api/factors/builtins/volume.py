"""量能类因子：17 日、20 日量比（基于指数数据）。"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue
from quant_etf_api.domain.common.bar_metrics import calc_volume_ratio_17d, calc_volume_ratio_20d


class VolumeRatio20dComputer:
    """20 日量比因子计算器。

    量比 = 当日成交量 / 近 20 个交易日平均成交量。
    直接复用 domain.common.bar_metrics.calc_volume_ratio_20d，保持计算逻辑单一来源。
    数据不足时返回 None（区分"无数据"与"量比恰好为 1"）。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 20 日量比的因子元数据。"""
        return FactorSpec(
            factor_id="volume_ratio_20d",
            name="20日量比",
            category="volume",
            version="2.0.0",
            description="指数当日成交量与近 20 个交易日平均成交量的比值，量比>1 表示相对放量。",
            required_data=["index_bars"],
            lookback_days=40,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 20 日量比。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        ratio = calc_volume_ratio_20d(index_code, trade_date, ctx.index_bars)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=ratio,
            payload={"lookback_days": 20},
        )


class VolumeRatio17dComputer:
    """17 日量比因子计算器。

    量比 = 当日成交量 / 近 17 个交易日平均成交量。
    直接复用 domain.common.bar_metrics.calc_volume_ratio_17d，保持计算逻辑单一来源。
    数据不足时返回 None（区分"无数据"与"量比恰好为 1"）。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 17 日量比的因子元数据。"""
        return FactorSpec(
            factor_id="volume_ratio_17d",
            name="17日量比",
            category="volume",
            version="1.0.0",
            description="指数当日成交量与近 17 个交易日平均成交量的比值，量比>1 表示相对放量。",
            required_data=["index_bars"],
            lookback_days=35,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 17 日量比。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        ratio = calc_volume_ratio_17d(index_code, trade_date, ctx.index_bars)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=ratio,
            payload={"lookback_days": 17},
        )
