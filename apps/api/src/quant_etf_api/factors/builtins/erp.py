"""股权风险溢价（ERP）因子：基于指数 PE 和 LPR 计算。

ERP = 盈利收益率 - 无风险利率
    = (1 / PE × 100) - LPR 1年期

ERP 越高表示股票相对债券越有吸引力，常用于判断市场整体估值水平。
数据来源：PE 来自 index_valuation 表，LPR 来自 macro_indicator 表。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


class ERPComputer:
    """股权风险溢价（ERP）因子计算器。

    计算公式：ERP = (1 / PE × 100) - LPR 1年期。
    PE 来自 index_valuation 表（当日），LPR 来自 macro_indicator 表（最新报价）。
    ERP 为正表示股票预期收益高于无风险利率，越高越有吸引力。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 ERP 因子的元数据。"""
        return FactorSpec(
            factor_id="erp",
            name="股权风险溢价",
            category="valuation",
            version="1.0.0",
            description=(
                "股权风险溢价 ERP = (1/PE × 100) - LPR 1年期。"
                "ERP 越高表示股票相对债券越有吸引力。"
                "PE 来自 index_valuation 表，LPR 来自 macro_indicator 表。"
            ),
            required_data=["index_valuation", "macro_indicators"],
            lookback_days=730,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算股权风险溢价。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext，需包含 index_valuation 和 macro_indicators。

        Returns:
            FactorValue，PE 或 LPR 数据缺失时 numeric 为 None。
        """
        # 获取当日 PE
        val_row = ctx.index_valuation.get((index_code, trade_date))
        if val_row is None or val_row.pe is None or val_row.pe <= 0:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"index_code": index_code, "reason": "无有效 PE 数据"},
            )

        # 获取最新 LPR 1年期
        lpr_data = ctx.macro_indicators.get("lpr1y")
        if not lpr_data:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"index_code": index_code, "reason": "无 LPR 数据"},
            )
        # 取最新一期 LPR
        latest_lpr = max(lpr_data.values()) if lpr_data else None
        if latest_lpr is None:
            return FactorValue(
                factor_id=self.spec.factor_id, numeric=None,
                payload={"index_code": index_code, "reason": "LPR 值为空"},
            )

        pe = val_row.pe
        earnings_yield = 1.0 / pe * 100.0
        erp = round(earnings_yield - latest_lpr, 4)

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=erp,
            payload={
                "index_code": index_code,
                "pe": round(pe, 2),
                "earnings_yield": round(earnings_yield, 4),
                "lpr_1y": latest_lpr,
            },
        )
