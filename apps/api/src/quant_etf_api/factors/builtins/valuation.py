"""估值类因子：PE 百分位、PB 百分位、滚动窗口估值分位（基于指数数据）。

- ``pe_percentile`` / ``pb_percentile``：直接读取 index_valuation 表入库的历史
  百分位（2014-10 起的扩张窗口，含当日），因子值透传原始百分位（0-100）；
- ``valuation_percentile``：从 index_valuation 原始 PE/PB 现算最近 N 个交易日的
  滚动窗口百分位（口径与入库扩张分位一致，只是样本截成固定窗口），因子值不落库。

百分位越低表示越低估。
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping
from datetime import date
from typing import Any

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue

# 滚动估值分位可选的估值字段：pe=PE(TTM)，pb=PB，mean=PE 与 PB 滚动分位的算术平均
VALUATION_FIELDS: tuple[str, ...] = ("pe", "pb", "mean")
_FIELD_LABELS: dict[str, str] = {"pe": "PE", "pb": "PB", "mean": "PE/PB均值"}


def rolling_percentile_series(
    dates: list[date], values: list[float], window: int, min_periods: int
) -> dict[date, tuple[float, int]]:
    """按滚动窗口计算每个观测日的历史百分位（含当日），只用截至当日的数据。

    百分位口径与入库扩张分位（``akshare_index._calc_percentile``）一致：
    rank / (n − 1) × 100，rank = 窗口内 ≤ 当日值的个数 − 1（并列值计入自身），
    n 为窗口样本数；n == 1 时无可比历史，取 50 作为中性占位。窗口取截至当日
    （含）最近 ``window`` 个有效观测，因此窗口不短于全部历史时与扩张分位完全相同。

    Args:
        dates: 按日期升序的观测日（已剔除缺失值）。
        values: 与 dates 等长的估值原值。
        window: 窗口长度（有效观测个数）。
        min_periods: 最少样本数，窗口样本不足时该日不输出。

    Returns:
        {观测日: (百分位, 窗口样本数)}；样本不足的日期不在结果中。
    """
    result: dict[date, tuple[float, int]] = {}
    ordered: list[float] = []
    for index, (row_date, value) in enumerate(zip(dates, values)):
        bisect.insort(ordered, value)
        if index >= window:
            # 滑出窗口的最早观测：按值删除一个即可（并列值任删其一结果相同）
            ordered.pop(bisect.bisect_left(ordered, values[index - window]))
        n = len(ordered)
        if n < min_periods:
            continue
        if n <= 1:
            result[row_date] = (50.0, n)
            continue
        rank = bisect.bisect_right(ordered, value) - 1
        result[row_date] = (round(rank / (n - 1) * 100, 2), n)
    return result


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
            lookback_days=730,
            # 历史百分位口径（0-100），可直接作为择时得分
            native_0_100=True,
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

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量读取各交易日估值百分位（只读当日估值行，天然无前视）。"""
        return {trade_date: self.compute(index_code, trade_date, ctx) for trade_date in dates}


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
            lookback_days=730,
            # 历史百分位口径（0-100），可直接作为择时得分
            native_0_100=True,
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

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量读取各交易日估值百分位（只读当日估值行，天然无前视）。"""
        return {trade_date: self.compute(index_code, trade_date, ctx) for trade_date in dates}


class ValuationPercentileComputer:
    """滚动窗口估值分位因子计算器（``valuation_percentile`` 模板）。

    从 index_valuation 的原始 PE / PB 现算最近 ``window`` 个交易日（有效观测）的
    历史百分位，解决扩张窗口分位被 2015 年泡沫等远期样本"压住"、高分位阈值
    永不触发的问题。只用截至目标日（含）的估值行，天然无前视；目标日没有有效
    估值、或窗口样本少于 ``min(min_periods, window)`` 时返回缺失。
    ``field=mean`` 取 PE 与 PB 滚动分位的算术平均（两者都需有值）。
    """

    def __init__(self, params: Mapping[str, Any]) -> None:
        """初始化计算器。

        Args:
            params: 已规范化的模板参数（field / window / min_periods）。
        """
        self._field = str(params["field"])
        self._window = int(params["window"])
        # 最少样本不可能超过窗口长度：超出时按窗口长度截断
        self._min_periods = min(int(params["min_periods"]), self._window)

    @property
    def spec(self) -> FactorSpec:
        """返回滚动估值分位的因子元数据（计算器自述口径）。"""
        label = _FIELD_LABELS.get(self._field, self._field)
        return FactorSpec(
            factor_id=f"valuation_percentile_{self._field}_{self._window}",
            name=f"{label}滚动分位（{self._window}日）",
            category="valuation",
            version="1.0.0",
            description=(
                "从 index_valuation 原始 PE/PB 现算最近 N 个交易日的滚动历史百分位（0-100）。"
            ),
            required_data=["index_valuation"],
            native_0_100=True,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单日滚动估值分位（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算各交易日的滚动估值分位。

        先按字段把估值行整理为有效观测序列并一次性滑动出全部观测日的百分位，
        再按目标日取值；某观测日的百分位只依赖截至该日（含）的观测。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，index_valuation 需覆盖目标日前 window 个交易日。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        fields = ("pe", "pb") if self._field == "mean" else (self._field,)
        # 同一上下文内 pe / pb / mean 等实例共享同窗口的分位序列
        per_field: dict[str, dict[date, tuple[float, int]]] = {
            name: ctx.memo(
                ("valuation_percentile", index_code, name, self._window, self._min_periods),
                lambda name=name: self._field_series(index_code, name, ctx),
            )
            for name in fields
        }

        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            parts: dict[str, float] = {}
            counts: dict[str, int] = {}
            reason = ""
            for name in fields:
                hit = per_field[name].get(trade_date)
                if hit is None:
                    reason = self._missing_reason(index_code, trade_date, name, ctx)
                    break
                parts[name], counts[name] = hit
            if reason:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"index_code": index_code, "reason": reason},
                )
                continue
            numeric = round(sum(parts.values()) / len(parts), 2)
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=numeric,
                payload={
                    "index_code": index_code,
                    "field": self._field,
                    "window": self._window,
                    "percentiles": parts,
                    "sample_counts": counts,
                },
            )
        return result

    def _field_series(
        self, index_code: str, name: str, ctx: FactorContext
    ) -> dict[date, tuple[float, int]]:
        """把单个估值字段整理为有效观测序列，并滑动计算全部观测日的百分位。

        Args:
            index_code: 指数代码。
            name: 估值字段（pe / pb）。
            ctx: FactorContext。

        Returns:
            {观测日: (百分位, 窗口样本数)}。
        """
        valuation_dates, rows = ctx.valuation_for(index_code)
        obs_dates: list[date] = []
        obs_values: list[float] = []
        for row_date, row in zip(valuation_dates, rows):
            value = getattr(row, name, None)
            if value is None:
                continue
            obs_dates.append(row_date)
            obs_values.append(float(value))
        return rolling_percentile_series(obs_dates, obs_values, self._window, self._min_periods)

    def _missing_reason(
        self, index_code: str, trade_date: date, name: str, ctx: FactorContext
    ) -> str:
        """区分"当日无有效估值"与"窗口样本不足"两种缺失原因。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            name: 估值字段（pe / pb）。
            ctx: FactorContext。

        Returns:
            缺失原因说明。
        """
        row = ctx.index_valuation.get((index_code, trade_date))
        if row is None or getattr(row, name, None) is None:
            return f"无当日有效 {name.upper()} 数据"
        return f"{name.upper()} 历史样本不足 {self._min_periods} 个交易日"
