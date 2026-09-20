"""量能类因子：N 日量比与 N 日成交额量比（基于指数数据）。"""

from __future__ import annotations

import bisect
from datetime import date

from quant_etf_api.domain.common.bar_metrics import calc_volume_ratio
from quant_etf_api.factors.base import (
    FactorContext,
    FactorSpec,
    FactorValue,
    period_lookback_days,
)


class VolumeRatioComputer:
    """N 日量比因子计算器。

    量比 = 当日成交量 / 近 N 个交易日平均成交量。
    直接复用 domain.common.bar_metrics.calc_volume_ratio，保持计算逻辑单一来源。
    数据不足时返回 None（区分"无数据"与"量比恰好为 1"）。

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
            version="2.0.0",
            description=(
                f"指数当日成交量与近 {self._period} 个交易日平均成交量的比值，"
                "量比>1 表示相对放量。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 N 日量比。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        ratio = calc_volume_ratio(index_code, trade_date, ctx.index_bars, self._period)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=ratio,
            payload={"period": self._period},
        )


def _build_sorted_turnovers(
    index_code: str,
    ctx: FactorContext,
) -> tuple[list[date], list[float]]:
    """提取指定指数的有序成交额序列，供批量计算复用。

    Args:
        index_code: 指数代码。
        ctx: FactorContext，包含全量回望数据。

    Returns:
        (turnover_dates, turnover_values) 元组，均按日期升序排列。
    """
    rows = sorted(
        [
            (dt, v.turnover)
            for (code, dt), v in ctx.index_bars.items()
            if code == index_code and v.turnover is not None
        ],
        key=lambda x: x[0],
    )
    if not rows:
        return [], []
    return [d for d, _ in rows], [t for _, t in rows]


class AmountRatioComputer:
    """N 日成交额量比因子计算器。

    成交额量比 = 当日成交额 / 近 N 个交易日平均成交额。
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
            version="1.0.0",
            description=(
                f"指数当日成交额与近 {self._period} 个交易日平均成交额的比值，"
                "量比>1 表示相对放量，反映资金参与度变化。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 N 日成交额量比。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，数据不足时 numeric 为 None。
        """
        today_bar = ctx.index_bars.get((index_code, trade_date))
        if today_bar is None or today_bar.turnover is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "当日无成交额数据"},
            )
        # 按日期升序取最近 period 个交易日成交额（与批量实现口径一致）
        past = sorted(
            [
                (dt, v.turnover)
                for (code, dt), v in ctx.index_bars.items()
                if code == index_code and dt < trade_date and v.turnover is not None
            ],
            key=lambda x: x[0],
        )
        past_values = [t for _, t in past]
        recent = past_values[-self._period :]
        if not recent:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "历史成交额数据不足"},
            )
        avg = sum(recent) / len(recent)
        if avg <= 0:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "平均成交额异常"},
            )
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(today_bar.turnover / avg, 4),
            payload={"period": self._period, "sample_count": len(recent)},
        )

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
        result: dict[date, FactorValue] = {}
        t_dates, t_values = _build_sorted_turnovers(index_code, ctx)
        if not t_dates:
            return {d: FactorValue(factor_id=self.spec.factor_id, numeric=None) for d in dates}

        for trade_date in dates:
            idx = bisect.bisect_right(t_dates, trade_date) - 1
            if idx < 0 or t_dates[idx] != trade_date:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": "当日无成交额数据"},
                )
                continue
            window = t_values[max(0, idx - self._period) : idx]
            if not window:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": "历史成交额数据不足"},
                )
                continue
            avg = sum(window) / len(window)
            if avg <= 0:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": "平均成交额异常"},
                )
                continue
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(t_values[idx] / avg, 4),
                payload={"period": self._period, "sample_count": len(window)},
            )
        return result

