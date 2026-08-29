"""宏观状态因子：基于 macro_indicator 表的 PMI 动量（基于指数数据）。

PMI（制造业采购经理指数）为月度指标，50 为荣枯线。
本模块提供 PMI 3 个月动量因子，用于判断宏观经济扩张/收缩趋势，
适合作为中线策略的仓位/风格开关（市场级因子，对所有指数取值相同）。
"""

from __future__ import annotations

from datetime import date, timedelta

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue
from quant_etf_api.factors.macro_period import parse_macro_period


class PMIMomentumComputer:
    """PMI 3 个月动量因子计算器。

    动量 = 截止 trade_date 的最新 PMI - 约 3 个月前（90 天前）的 PMI。
    动量为正表示经济扩张趋势加强，为负表示趋势走弱。
    属于市场级因子：同一交易日所有指数返回相同值，
    适合用于 timing（代理指数读取）或 filter 阈值，不建议用于横截面评分。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 PMI 3 个月动量的因子元数据。"""
        return FactorSpec(
            factor_id="pmi_momentum_3m",
            name="PMI三个月动量",
            category="macro",
            version="1.0.0",
            description=(
                "最新制造业 PMI 与约 3 个月前 PMI 的差值，衡量经济扩张/收缩趋势变化。"
                "市场级因子，所有指数同值，适合择时或过滤。"
            ),
            required_data=["macro_indicators"],
            lookback_days=120,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 PMI 3 个月动量。

        Args:
            index_code: 指数代码（市场级因子，不参与计算）。
            trade_date: 目标交易日。
            ctx: FactorContext，需包含 macro_indicators["pmi"]。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        pmi_map = (ctx.macro_indicators or {}).get("pmi") or {}
        parsed: list[tuple[date, float]] = []
        for period, value in pmi_map.items():
            period_date = parse_macro_period(period)
            if period_date is not None and period_date <= trade_date:
                parsed.append((period_date, value))
        if not parsed:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "截止当日无 PMI 数据"},
            )

        parsed.sort(key=lambda x: x[0])
        latest_date, latest_value = parsed[-1]
        # 3 个月前：取截止日期 <= 最新期 - 90 天 的最近一期 PMI
        target = latest_date - timedelta(days=90)
        baseline: tuple[date, float] | None = None
        for period_date, value in parsed:
            if period_date <= target:
                baseline = (period_date, value)
            else:
                break
        if baseline is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "3 个月前无 PMI 数据", "latest_period": str(latest_date)},
            )

        momentum = round(latest_value - baseline[1], 2)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=momentum,
            payload={
                "latest_pmi": latest_value,
                "latest_period": str(latest_date),
                "baseline_pmi": baseline[1],
                "baseline_period": str(baseline[0]),
            },
        )
