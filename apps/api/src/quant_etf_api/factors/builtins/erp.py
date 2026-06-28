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
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"index_code": index_code, "reason": "无有效 PE 数据"},
            )

        # 获取最新 LPR 1年期
        lpr_data = ctx.macro_indicators.get("lpr1y")
        if not lpr_data:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"index_code": index_code, "reason": "无 LPR 数据"},
            )
        # 取最新一期 LPR
        latest_lpr = max(lpr_data.values()) if lpr_data else None
        if latest_lpr is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
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


class ERPPercentileComputer:
    """ERP 历史百分位因子计算器。

    计算当前 ERP 值在近 2 年历史分布中的百分位排名（0-100）。
    需要 FactorContext.index_valuation 包含多日历史数据（lookback_days=730）。
    ERP 百分位 > 80 表示股票相对债券极具吸引力，常用于熊市超跌判断。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 ERP 百分位因子的元数据。"""
        return FactorSpec(
            factor_id="erp_percentile",
            name="ERP 历史百分位",
            category="valuation",
            version="2.0.0",
            description=(
                "当前 ERP 在近 2 年历史分布中的百分位排名（0-100）。"
                "ERP 百分位 > 80 表示股票相对债券极具吸引力，用于熊市超跌判断。"
                "依赖 FactorContext.index_valuation 包含多日历史数据。"
            ),
            required_data=["index_valuation", "macro_indicators"],
            lookback_days=730,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 ERP 在历史分布中的百分位排名。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext，index_valuation 需包含 730 天历史数据。

        Returns:
            FactorValue，数据不足（< 10 个历史数据点）时 numeric 为 None。
        """
        # 获取 LPR（与 ERPComputer 保持一致，取最大值）
        lpr_data = ctx.macro_indicators.get("lpr1y", {})
        latest_lpr = max(lpr_data.values()) if lpr_data else None
        if latest_lpr is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"index_code": index_code, "reason": "无 LPR 数据"},
            )

        # 获取当日 PE（用于计算当日 ERP）
        current_row = ctx.index_valuation.get((index_code, trade_date))
        if current_row is None or current_row.pe is None or current_row.pe <= 0:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"index_code": index_code, "reason": "无当日有效 PE 数据"},
            )
        current_erp = 1.0 / current_row.pe * 100.0 - latest_lpr

        # 遍历历史估值数据，计算各历史日期的 ERP
        erp_history: list[float] = []
        for (code, _dt), val_row in ctx.index_valuation.items():
            if code != index_code:
                continue
            if val_row.pe is None or val_row.pe <= 0:
                continue
            erp_history.append(1.0 / val_row.pe * 100.0 - latest_lpr)

        if len(erp_history) < 10:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "index_code": index_code,
                    "reason": f"历史数据不足（{len(erp_history)} 点）",
                },
            )

        # 计算百分位：历史中有多少比例的 ERP <= 当前 ERP
        count_below = sum(1 for v in erp_history if v <= current_erp)
        percentile = round(count_below / len(erp_history) * 100, 2)

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=percentile,
            payload={
                "index_code": index_code,
                "current_erp": round(current_erp, 4),
                "history_count": len(erp_history),
                "lpr_1y": latest_lpr,
            },
        )
