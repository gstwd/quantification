"""份额流量类因子：ETF 份额日变化率（申赎流向）。"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


class ShareDeltaPctComputer:
    """ETF 份额日变化率因子计算器。

    读取 etf_daily_share.shares_delta_pct，正值代表净申购，负值代表净赎回。
    数据来源于 EtfDailyShareModel，部分 ETF 当日可能无份额数据（返回 numeric=None）。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回份额日变化率的因子元数据。"""
        return FactorSpec(
            factor_id="share_delta_pct",
            name="份额日变化率",
            category="flow",
            version="1.0.0",
            description=(
                "ETF 当日份额变化率（%），正值=净申购，负值=净赎回。"
                "来源于 etf_daily_share.shares_delta_pct，东方财富未覆盖的 ETF 返回 None。"
            ),
            required_data=["etf_shares"],
        )

    def compute(self, etf_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """读取指定 ETF 当日份额变化率。

        Args:
            etf_code: ETF 代码。
            trade_date: 目标交易日。
            ctx: FactorContext，份额数据来自 etf_shares。

        Returns:
            FactorValue，ETF 无当日份额数据时 numeric 为 None。
        """
        share_row = ctx.etf_shares.get((etf_code, trade_date))
        if share_row is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "无份额数据"},
            )
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=share_row.shares_delta_pct,
            payload={
                "shares_total": share_row.shares_total,
                "shares_delta": share_row.shares_delta,
            },
        )
