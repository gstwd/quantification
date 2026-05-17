"""量能类因子：20日量比。"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue
from quant_etf_api.services._bar_metrics import calc_volume_ratio_20d


class VolumeRatio20dComputer:
    """20日量比因子计算器。

    量比 = 当日成交量 / 近20个交易日平均成交量。
    直接复用 services._bar_metrics.calc_volume_ratio_20d，保持计算逻辑单一来源。
    数据不足时返回 1.0（中性值，与 _bar_metrics 的 fallback 一致）。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回20日量比的因子元数据。"""
        return FactorSpec(
            factor_id="volume_ratio_20d",
            name="20日量比",
            category="volume",
            version="1.0.0",
            description="当日成交量与近20个交易日平均成交量的比值，量比>1表示相对放量。",
            required_data=["etf_bars"],
        )

    def compute(self, etf_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算20日量比。

        Args:
            etf_code: ETF 代码。
            trade_date: 目标交易日。
            ctx: 含90天回望的 FactorContext。

        Returns:
            FactorValue，数据不足时 numeric 为 1.0（中性值）。
        """
        ratio = calc_volume_ratio_20d(etf_code, trade_date, ctx.etf_bars)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=ratio,
            payload={"lookback_days": 20},
        )
