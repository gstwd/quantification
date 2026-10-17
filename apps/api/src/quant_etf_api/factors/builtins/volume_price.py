"""量价类因子：QuantsPlaybook 第一批晋升因子（基于指数日线）。

- 成交额波动（标准化）、量价相关：华西《行业有效量价因子与行业轮动》；
- 价量共振：华创《成交量的奥秘：另类价量共振指标》。

计算口径直接复用 ``domain.research.signal_lab`` 的纯函数，与因子中心研究台、
离线沙箱保持一致；量额 ≤ 0 视为缺失，数据不足时 numeric 为 None 并给出中文原因。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from quant_etf_api.domain.research import signal_lab
from quant_etf_api.factors.base import (
    FactorContext,
    FactorSpec,
    FactorValue,
    period_lookback_days,
)

# 日线字段 → 领域函数使用的列名
_FIELD_COLUMNS = {"close_price": "close", "volume": "volume", "turnover": "amount"}


def _positive_frame(index_code: str, ctx: FactorContext, fields: tuple[str, ...]) -> pd.DataFrame:
    """取指定字段的日线序列并转为数据帧，非正数视为缺失。

    Args:
        index_code: 指数代码。
        ctx: FactorContext。
        fields: 日线字段名，如 ("close_price", "volume")。

    Returns:
        以交易日为索引、列名为领域口径（close/volume/amount）的数据帧。
    """
    series_dates, values = ctx.series(index_code, fields)
    frame = pd.DataFrame(
        [[float(v) for v in row] for row in values],
        index=pd.DatetimeIndex(series_dates),
        columns=[_FIELD_COLUMNS[f] for f in fields],
        dtype=float,
    )
    return frame.where(frame > 0)


def _finite(value: Any, digits: int = 6) -> float | None:
    """安全取整：None/NaN/inf 返回 None。"""
    if value is None:
        return None
    number = float(value)
    return round(number, digits) if np.isfinite(number) else None


def _map_series(
    values: pd.Series,
    dates: list[date],
    factor_id: str,
    params: Mapping[str, Any],
    payload_extra: Callable[[pd.Timestamp], dict[str, Any]] | None = None,
    digits: int = 6,
) -> dict[date, FactorValue]:
    """把整段因子序列映射为逐日 FactorValue。

    Args:
        values: 以交易日为索引的因子序列。
        dates: 需要取值的交易日。
        factor_id: 计算器自述标识。
        params: 写入 payload 的参数。
        payload_extra: 按日期补充 payload 的函数（可选）。
        digits: 数值保留小数位。

    Returns:
        key=交易日, value=FactorValue 的字典。
    """
    result: dict[date, FactorValue] = {}
    for trade_date in dates:
        stamp = pd.Timestamp(trade_date)
        payload: dict[str, Any] = dict(params)
        if stamp not in values.index:
            payload["reason"] = "当日无有效日线数据"
            result[trade_date] = FactorValue(factor_id=factor_id, numeric=None, payload=payload)
            continue
        numeric = _finite(values.loc[stamp], digits)
        if numeric is None:
            payload["reason"] = "历史数据不足或窗口内含缺失（量额为 0 视为缺失）"
            result[trade_date] = FactorValue(factor_id=factor_id, numeric=None, payload=payload)
            continue
        if payload_extra is not None:
            payload.update(payload_extra(stamp))
        result[trade_date] = FactorValue(factor_id=factor_id, numeric=numeric, payload=payload)
    return result


class AmountVolatilityComputer:
    """成交额波动（标准化）因子计算器。

    取值 = −Std(成交额, N) / Mean(成交额, N)：成交额越稳定取值越高。原版用原始标准差，
    跨指数量级不可比，这里除以均值做标准化。实现 BatchFactorComputer 协议。

    Attributes:
        _period: 窗口交易日数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 20) -> None:
        """初始化计算器。

        Args:
            period: 窗口交易日数。

        Raises:
            ValueError: period 小于 2。
        """
        if period < 2:
            raise ValueError("period 必须不小于 2")
        self._period = int(period)
        self._lookback = period_lookback_days(self._period)

    @property
    def spec(self) -> FactorSpec:
        """返回成交额波动的因子元数据。"""
        return FactorSpec(
            factor_id=f"amount_volatility_{self._period}d",
            name=f"{self._period}日成交额波动（标准化）",
            category="volume",
            version="1.0.0",
            description=(
                f"−Std(成交额, {self._period}) / Mean(成交额, {self._period})，"
                "成交额越稳定取值越高；成交额缺失或为 0 时为空。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单日取值（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算成交额波动。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        frame = _positive_frame(index_code, ctx, ("turnover",))
        values = signal_lab.factor_amount_volatility(frame, {"d": self._period})
        return _map_series(values, dates, self.spec.factor_id, {"period": self._period})


class PriceVolumeCorrComputer:
    """量价相关因子计算器。

    取值 = −Corr(收盘价, 成交量, N)：量价背离（放量下跌 / 缩量上涨）时取值高。
    实现 BatchFactorComputer 协议。

    Attributes:
        _period: 窗口交易日数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, period: int = 20) -> None:
        """初始化计算器。

        Args:
            period: 窗口交易日数。

        Raises:
            ValueError: period 小于 3。
        """
        if period < 3:
            raise ValueError("period 必须不小于 3")
        self._period = int(period)
        self._lookback = period_lookback_days(self._period)

    @property
    def spec(self) -> FactorSpec:
        """返回量价相关的因子元数据。"""
        return FactorSpec(
            factor_id=f"price_volume_corr_{self._period}d",
            name=f"{self._period}日量价相关",
            category="volume",
            version="1.0.0",
            description=(
                f"−Corr(收盘价, 成交量, {self._period})，量价背离时取值高；"
                "成交量缺失或为 0 时为空。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单日取值（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算量价相关。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        frame = _positive_frame(index_code, ctx, ("close_price", "volume"))
        values = signal_lab.factor_price_volume_corr(frame, {"d": self._period})
        return _map_series(values, dates, self.spec.factor_id, {"period": self._period})


def pv_resonance_lookback_days(params: Mapping[str, Any]) -> int:
    """价量共振所需回望自然日数：取 HMA 与市场划分均线中最长的有效窗口。

    Args:
        params: 已规范化参数。

    Returns:
        回望自然日数。
    """
    bma, ama = int(params["bma_window"]), int(params["ama_window"])
    longest = max(
        ama + int(math.sqrt(ama)),
        int(params["slow_window"]),
        bma + int(math.sqrt(bma)) + int(params["lag"]),
    )
    return period_lookback_days(longest)


class PvResonanceComputer:
    """价量共振因子计算器。

    指标 = [HMA(C,bma)/HMA(C,bma)_{t−lag}] × [HMA(V,5)/HMA(V,ama)]；
    MA(C,fast) > MA(C,slow) 为多头市场取 bull_threshold，否则取 bear_threshold。
    输出共振比 = 指标 / 当日阈值，> 1 即研报开仓条件；Filter 用 ``gt 1``，
    Timing 配合 ``resonance_score`` 变换。实现 BatchFactorComputer 协议。

    Attributes:
        _params: 已规范化参数。
        _lookback: 所需自然日回望窗口。
    """

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        """初始化计算器。

        Args:
            params: 参数字典，缺省项取研报默认值。

        Raises:
            ValueError: 参数未声明或越界。
        """
        definition = signal_lab.TIMING_SIGNALS["pv_resonance"]
        self._params = signal_lab.resolve_params(definition, dict(params or {}))
        self._lookback = pv_resonance_lookback_days(self._params)

    @property
    def spec(self) -> FactorSpec:
        """返回价量共振的因子元数据。"""
        return FactorSpec(
            factor_id="pv_resonance",
            name="价量共振",
            category="volume",
            version="1.0.0",
            description=(
                "价格 HMA 动量 × 量能 HMA 比值，除以按均线多空划分的开仓阈值得到共振比，"
                "> 1 为开仓条件；成交量缺失时为空。"
            ),
            required_data=["index_bars"],
            lookback_days=self._lookback,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单日取值（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算价量共振比。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典；payload 附指标值、阈值与市场划分。
        """
        frame = _positive_frame(index_code, ctx, ("close_price", "volume"))
        out = signal_lab.signal_pv_resonance(frame, self._params)
        ratio = out["value"] / out["threshold"]
        bull = float(self._params["bull_threshold"])

        def _extra(stamp: pd.Timestamp) -> dict[str, Any]:
            """补充当日中间分量。"""
            row = out.loc[stamp]
            return {
                "value": _finite(row["value"]),
                "threshold": _finite(row["threshold"]),
                "regime": "bull" if row["threshold"] == bull else "bear",
                "price_mom": _finite(row["price_mom"]),
                "vol_mom": _finite(row["vol_mom"]),
            }

        return _map_series(ratio, dates, self.spec.factor_id, self._params, _extra, digits=4)
