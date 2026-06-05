"""估值类因子：PE 百分位、PB 百分位（基于指数数据）。

直接读取指数的估值数据（index_valuation 表），
将 PE/PB 历史百分位映射为因子值。
百分位越低表示越低估，因子值直接透传原始百分位（0-100）。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


class PEPercentileComputer:
    """PE 百分位因子计算器。

    读取指数的 PE(TTM) 历史百分位（0-100），
    百分位越低表示当前估值越便宜。
    仅沪深 300/上证 50/中证 500 等主要宽基指数有数据覆盖。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 PE 百分位的因子元数据。"""
        return FactorSpec(
            factor_id="pe_percentile",
            name="PE百分位",
            category="valuation",
            version="2.0.0",
            description=(
                "指数的 PE(TTM) 历史百分位（0-100），数值越低越低估。"
                "数据来源于 index_valuation 表，仅主要宽基指数有覆盖。"
            ),
            required_data=["index_valuation"],
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 PE 百分位。

        直接用 index_code 从 index_valuation 中读取对应日期的 pe_percentile。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，无估值数据时 numeric 为 None。
        """
        val_row = ctx.index_valuation.get((index_code, trade_date))
        if val_row is None or val_row.pe_percentile is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"index_code": index_code, "reason": "无估值数据"},
            )

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(val_row.pe_percentile, 2),
            payload={
                "index_code": index_code,
                "pe": val_row.pe,
            },
        )


class PBPercentileComputer:
    """PB 百分位因子计算器。

    读取指数的 PB 历史百分位（0-100），
    百分位越低表示当前估值越便宜。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 PB 百分位的因子元数据。"""
        return FactorSpec(
            factor_id="pb_percentile",
            name="PB百分位",
            category="valuation",
            version="2.0.0",
            description=(
                "指数的 PB 历史百分位（0-100），数值越低越低估。"
                "数据来源于 index_valuation 表，仅主要宽基指数有覆盖。"
            ),
            required_data=["index_valuation"],
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 PB 百分位。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，无估值数据时 numeric 为 None。
        """
        val_row = ctx.index_valuation.get((index_code, trade_date))
        if val_row is None or val_row.pb_percentile is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"index_code": index_code, "reason": "无估值数据"},
            )

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(val_row.pb_percentile, 2),
            payload={
                "index_code": index_code,
                "pb": val_row.pb,
            },
        )
