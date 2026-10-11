"""量能类因子：N 日量比与 N 日成交额量比（基于指数数据）。

两者共用同一实现：当日取值 / 当日之前恰好 N 个有效交易日的均值。历史不足 N 个
有效样本时返回 None，与其它窗口类因子一致，不在预热期用不完整窗口输出数值。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.factors.base import (
    FactorContext,
    FactorSpec,
    FactorValue,
    index_on,
    period_lookback_days,
)


def _ratio_batch(
    index_code: str,
    dates: list[date],
    ctx: FactorContext,
    field: str,
    period: int,
    factor_id: str,
    label: str,
) -> dict[date, FactorValue]:
    """批量计算「当日值 / 前 N 个有效交易日均值」的量比类因子。

    Args:
        index_code: 指数代码。
        dates: 需要计算的交易日列表（升序）。
        ctx: FactorContext，包含全量回望数据。
        field: 日线字段名，volume 或 turnover。
        period: 均值窗口（交易日）。
        factor_id: 计算器自述的因子标识。
        label: 字段中文名，用于缺失原因。

    Returns:
        key=交易日, value=FactorValue 的字典。
    """
    series_dates, values = ctx.series(index_code, (field,))
    result: dict[date, FactorValue] = {}
    for trade_date in dates:
        idx = index_on(series_dates, trade_date)
        if idx is None:
            result[trade_date] = FactorValue(
                factor_id=factor_id,
                numeric=None,
                payload={"reason": f"当日无{label}数据", "period": period},
            )
            continue
        if idx < period:
            result[trade_date] = FactorValue(
                factor_id=factor_id,
                numeric=None,
                payload={"reason": f"历史{label}数据不足 {period} 条", "period": period},
            )
            continue
        window = [value[0] for value in values[idx - period : idx]]
        avg = sum(window) / period
        if avg <= 0:
            result[trade_date] = FactorValue(
                factor_id=factor_id,
                numeric=None,
                payload={"reason": f"平均{label}异常", "period": period},
            )
            continue
        result[trade_date] = FactorValue(
            factor_id=factor_id,
            numeric=round(values[idx][0] / avg, 4),
            payload={"period": period, "sample_count": period},
        )
    return result


class VolumeRatioComputer:
    """N 日量比因子计算器。

    量比 = 当日成交量 / 当日之前 N 个交易日平均成交量。
    数据不足时返回 None（区分"无数据"与"量比恰好为 1"）。
    实现 BatchFactorComputer 协议，支持回测批量预计算。

    Attributes:
        _period: 回望交易日数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 20) -> None:
        """初始化量比计算器。

        Args:
            period: 回望交易日数，如 17/20/60。

        Raises:
            ValueError: period 小于 1。
        """
        if period < 1:
            raise ValueError("period 必须不小于 1")
        self._period = int(period)
        self._lookback = period_lookback_days(self._period)

    @property
    def spec(self) -> FactorSpec:
        """返回 N 日量比的因子元数据。"""
        return FactorSpec(
            factor_id=f"volume_ratio_{self._period}d",
            name=f"{self._period}日量比",
            category="volume",
            version="3.0.0",
            description=(
                f"指数当日成交量与此前 {self._period} 个交易日平均成交量的比值，"
                "量比>1 表示相对放量；历史不足 N 日时为空。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 N 日量比（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 N 日量比。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        return _ratio_batch(
            index_code, dates, ctx, "volume", self._period, self.spec.factor_id, "成交量"
        )


class AmountRatioComputer:
    """N 日成交额量比因子计算器。

    成交额量比 = 当日成交额 / 当日之前 N 个交易日平均成交额。
    与 volume_ratio 的区别：使用 index_daily_bar 的 turnover 字段
    （成交额，单位元）而非 volume（成交量，单位手），反映资金参与度。
    数据不足时返回 None（区分"无数据"与"量比恰好为 1"）。
    实现 BatchFactorComputer 协议，支持回测批量预计算。

    Attributes:
        _period: 回望交易日数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 20) -> None:
        """初始化成交额量比计算器。

        Args:
            period: 回望交易日数，默认 20。

        Raises:
            ValueError: period 小于 1。
        """
        if period < 1:
            raise ValueError("period 必须不小于 1")
        self._period = int(period)
        self._lookback = period_lookback_days(self._period)

    @property
    def spec(self) -> FactorSpec:
        """返回 N 日成交额量比的因子元数据。"""
        return FactorSpec(
            factor_id=f"amount_ratio_{self._period}d",
            name=f"{self._period}日成交额量比",
            category="volume",
            version="2.0.0",
            description=(
                f"指数当日成交额与此前 {self._period} 个交易日平均成交额的比值，"
                "量比>1 表示相对放量，反映资金参与度变化；历史不足 N 日时为空。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 N 日成交额量比（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 N 日成交额量比。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        return _ratio_batch(
            index_code, dates, ctx, "turnover", self._period, self.spec.factor_id, "成交额"
        )
