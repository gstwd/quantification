"""宏观状态因子：基于 macro_indicator 表的 PMI 动量（基于指数数据）。

PMI（制造业采购经理指数）为月度指标，50 为荣枯线。
本模块提供 PMI 动量因子，用于判断宏观经济扩张/收缩趋势，
适合作为中线策略的仓位/风格开关。
"""

from __future__ import annotations

import bisect
from datetime import date, timedelta

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue

# PMI 为月度指标，回看月数按每月 30 天折算为自然日
_DAYS_PER_MONTH = 30


def pmi_lookback_days(months: int) -> int:
    """按 PMI 动量回看月数推导回望自然日数。

    Args:
        months: 回看月数。

    Returns:
        回望自然日数，至少 120 天（覆盖月报发布时滞与缺口）。
    """
    return max(120, int(months) * _DAYS_PER_MONTH * 2)


class PMIMomentumComputer:
    """PMI 动量因子计算器。

    动量 = 截止 trade_date 已公布的最新 PMI - 约 N 个月前（N×30 天前）的 PMI。
    参考月 PMI 于次月 1 日起才可见，避免参考月当月使用尚未公布的数据。
    动量为正表示经济扩张趋势加强，为负表示趋势走弱。

    Attributes:
        _months: 回看月数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, months: int = 3) -> None:
        """初始化 PMI 动量计算器。

        Args:
            months: 回看月数，默认 3。

        Raises:
            ValueError: months 小于 1。
        """
        if months < 1:
            raise ValueError("months 必须不小于 1")
        self._months = int(months)
        self._lookback = pmi_lookback_days(self._months)

    @property
    def spec(self) -> FactorSpec:
        """返回 PMI 动量的因子元数据。"""
        return FactorSpec(
            factor_id=f"pmi_momentum_{self._months}m",
            name=f"PMI{self._months}个月动量",
            category="macro",
            version="2.0.0",
            description=(
                f"最新制造业 PMI 与约 {self._months} 个月前 PMI 的差值，"
                "衡量经济扩张/收缩趋势变化。"
            ),
            required_data=["macro_indicators"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 PMI 动量（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算各交易日的 PMI 动量。

        只使用截止目标日**已公布**的 PMI（参考月次月 1 日起可见，见
        macro_period.macro_available_date）；动量的期差按参考月计算。

        Args:
            index_code: 指数代码（市场级口径，不参与计算）。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，需包含 macro_indicators["pmi"]。

        Returns:
            key=交易日, value=FactorValue 的字典；数据不足时 numeric 为 None。
        """
        records = ctx.macro_series("pmi")
        available = [record[0] for record in records]
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            visible = records[: bisect.bisect_right(available, trade_date)]
            if not visible:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": "截止当日无已公布 PMI 数据", "months": self._months},
                )
                continue
            # 可见日与参考月同序：最后一条即最新已公布的参考月
            _, latest_period, _, latest_value = visible[-1]
            # N 个月前：取参考月 <= 最新期 − N×30 天 的最近一期 PMI
            target = latest_period - timedelta(days=_DAYS_PER_MONTH * self._months)
            baseline: tuple[date, float] | None = None
            for _, period_date, _, value in visible:
                if period_date <= target:
                    baseline = (period_date, value)
                else:
                    break
            if baseline is None:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={
                        "reason": f"{self._months} 个月前无 PMI 数据",
                        "latest_period": str(latest_period),
                        "months": self._months,
                    },
                )
                continue
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(latest_value - baseline[1], 2),
                payload={
                    "months": self._months,
                    "latest_pmi": latest_value,
                    "latest_period": str(latest_period),
                    "baseline_pmi": baseline[1],
                    "baseline_period": str(baseline[0]),
                },
            )
        return result
