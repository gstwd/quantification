"""市场宽度因子：全市场活跃指数中收盘价站上均线的比例（基于指数数据）。

市场宽度衡量"当前有多少指数处于上升趋势"，属于市场级因子：
同一交易日所有指数返回相同值，适合用于择时（代理指数读取）或过滤阈值，
不建议用于横截面评分。

因子元数据：value_shape=market、
usage=[timing, filter]——配置校验会拒绝将其放入 score/rank。

由于该因子需要全市场行情，FactorSpec.market_scope=True，
实时与回测都会按 point-in-time 口径（区间起点仍存续的指数）额外加载全市场
日线数据作为因子上下文。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import (
    FactorContext,
    FactorSpec,
    FactorValue,
    USAGE_FILTER,
    USAGE_TIMING,
    VALUE_SHAPE_MARKET,
    index_on,
    period_lookback_days,
)


class BreadthMAComputer:
    """MA 市场宽度因子计算器。

    对每个交易日统计全市场（上下文内全部指数）中收盘价高于自身 MAN
    的指数占比（0-100）。数据不足 N 条收盘价的指数不参与统计。
    实现 BatchFactorComputer 协议，回测预计算一次覆盖所有交易日。

    Attributes:
        _period: 均线周期（交易日）。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 20) -> None:
        """初始化市场宽度计算器。

        Args:
            period: 均线周期（交易日），默认 20。

        Raises:
            ValueError: period 小于 2。
        """
        if period < 2:
            raise ValueError("period 必须至少为 2")
        self._period = int(period)
        self._lookback = period_lookback_days(self._period)

    @property
    def spec(self) -> FactorSpec:
        """返回 MA 市场宽度的因子元数据。"""
        return FactorSpec(
            factor_id=f"breadth_ma{self._period}_pct",
            name=f"MA{self._period}市场宽度",
            category="technical",
            version="1.0.0",
            description=(
                f"全市场活跃指数中收盘价站上自身 {self._period} 日均线的占比（0-100），"
                "衡量上升趋势扩散程度。市场级因子，适合择时或过滤。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
            market_scope=True,
            value_shape=VALUE_SHAPE_MARKET,
            usage=[USAGE_TIMING, USAGE_FILTER],
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 MA 市场宽度（委托批量实现，保证与批量口径一致）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 MA 市场宽度。

        市场宽度为市场级因子，对传入的任一指数返回相同序列（现算服务对市场级
        模板只计算一次再广播）。只统计当日有收盘价且此前收盘价足够 N 条的指数，
        均线用前缀和 O(1) 求得。

        Args:
            index_code: 指数代码（市场级因子，不参与计算）。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全市场回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        per_code: list[tuple[list[date], list[float], list[float]]] = []
        for code in ctx.bar_codes():
            code_dates, code_prices = ctx.closes(code)
            if len(code_dates) < self._period:
                continue
            prefix = [0.0]
            for price in code_prices:
                prefix.append(prefix[-1] + price)
            per_code.append((code_dates, code_prices, prefix))

        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            above_count = 0
            valid_count = 0
            for code_dates, code_prices, prefix in per_code:
                idx = index_on(code_dates, trade_date)
                if idx is None or idx < self._period - 1:
                    continue
                ma = (prefix[idx + 1] - prefix[idx + 1 - self._period]) / self._period
                valid_count += 1
                if code_prices[idx] > ma:
                    above_count += 1
            if valid_count == 0:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": "全市场无足够数据", "period": self._period},
                )
                continue
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(above_count / valid_count * 100, 2),
                payload={
                    "period": self._period,
                    "above_count": above_count,
                    "valid_indexes": valid_count,
                },
            )
        return result
