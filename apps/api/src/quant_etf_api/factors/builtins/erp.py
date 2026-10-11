"""股权风险溢价（ERP）因子：基于指数 PE 和 LPR 计算。

ERP = 盈利收益率 - 无风险利率
    = (1 / PE × 100) - LPR 1年期

ERP 越高表示股票相对债券越有吸引力，常用于判断市场整体估值水平。
数据来源：PE 来自 index_valuation 表，LPR 来自 macro_indicator 表。

两个计算器都实现批量接口，且只使用截止目标日已公布的数据：估值按目标日
精确取行，LPR 按发布可见日二分取「截止当日的最近一期」。批量路径不经过逐日
时点化上下文，因此时点安全由计算器自身保证。
"""

from __future__ import annotations

import bisect
from datetime import date, timedelta
from typing import Any

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue

# ERP 百分位的历史窗口（自然日）：固定为目标日前 2 年，与上下文实际加载
# 多少历史无关，保证实时（固定回望）与回测（长预加载）口径一致。
_ERP_HISTORY_DAYS = 730
# ERP 百分位所需的最少历史样本数
_ERP_MIN_HISTORY = 10
_LPR_CODE = "lpr1y"


class _LprLookup:
    """按发布可见日二分查询「截止某日已公布的最近一期 LPR」。"""

    def __init__(self, ctx: FactorContext) -> None:
        """从上下文的宏观序列构建查询表。

        Args:
            ctx: FactorContext，需包含 macro_indicators["lpr1y"]。
        """
        records = ctx.macro_series(_LPR_CODE)
        self._available = [record[0] for record in records]
        self._records = records

    def __bool__(self) -> bool:
        """是否存在任何 LPR 记录。"""
        return bool(self._records)

    def as_of(self, trade_date: date) -> tuple[str, float] | None:
        """返回截止 trade_date 已公布的最近一期 (period, value)。

        Args:
            trade_date: 目标日期。

        Returns:
            (period 原文, 数值)；截止当日无已公布记录时为 None。
        """
        idx = bisect.bisect_right(self._available, trade_date) - 1
        if idx < 0:
            return None
        _, _, period, value = self._records[idx]
        return period, value


def _valid_pe(row: Any) -> float | None:
    """返回估值行中可用于计算盈利收益率的 PE，非正或缺失时为 None。"""
    if row is None:
        return None
    pe = getattr(row, "pe", None)
    if pe is None or pe <= 0:
        return None
    return float(pe)


class ERPComputer:
    """股权风险溢价（ERP）因子计算器。

    计算公式：ERP = (1 / PE × 100) - LPR 1年期。
    PE 来自 index_valuation 表（当日），LPR 来自 macro_indicator 表
    （截止 trade_date 已公布的最近一期报价，避免使用未来才公布的 LPR）。
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
        """计算股权风险溢价（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算各交易日的股权风险溢价。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，需包含 index_valuation 和 macro_indicators。

        Returns:
            key=交易日, value=FactorValue 的字典；PE 或 LPR 缺失时 numeric 为 None。
        """
        lpr = _LprLookup(ctx)
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            row = ctx.index_valuation.get((index_code, trade_date))
            pe = _valid_pe(row)
            if pe is None:
                result[trade_date] = self._missing(index_code, "无有效 PE 数据")
                continue
            if not lpr:
                result[trade_date] = self._missing(index_code, "无 LPR 数据")
                continue
            latest = lpr.as_of(trade_date)
            if latest is None:
                result[trade_date] = self._missing(index_code, "截止当日无有效 LPR 数据")
                continue
            latest_period, latest_lpr = latest
            earnings_yield = 1.0 / pe * 100.0
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(earnings_yield - latest_lpr, 4),
                payload={
                    "index_code": index_code,
                    "pe": round(pe, 2),
                    "earnings_yield": round(earnings_yield, 4),
                    "lpr_1y": latest_lpr,
                    "lpr_period": latest_period,
                },
            )
        return result

    def _missing(self, index_code: str, reason: str) -> FactorValue:
        """构造缺失值结果。"""
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=None,
            payload={"index_code": index_code, "reason": reason},
        )


class ERPPercentileComputer:
    """ERP 历史百分位因子计算器。

    计算当前 ERP 在目标日前 2 年（730 自然日）历史 ERP 分布中的百分位（0-100）。
    历史上每个估值日的 ERP 使用**该日已公布**的 LPR，而不是统一套用当前 LPR——
    后者等价于 1/PE 的百分位，利率变化完全不起作用。
    ERP 百分位 > 80 表示股票相对债券极具吸引力，常用于熊市超跌判断。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 ERP 百分位因子的元数据。"""
        return FactorSpec(
            factor_id="erp_percentile",
            name="ERP 历史百分位",
            category="valuation",
            version="3.0.0",
            description=(
                "当前 ERP 在目标日前 2 年历史 ERP 分布中的百分位排名（0-100），"
                "历史各日 ERP 使用当日已公布的 LPR。"
                "ERP 百分位 > 80 表示股票相对债券极具吸引力，用于熊市超跌判断。"
            ),
            required_data=["index_valuation", "macro_indicators"],
            lookback_days=_ERP_HISTORY_DAYS,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 ERP 历史百分位（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算各交易日的 ERP 历史百分位。

        先按估值日一次性算出每日 ERP（各用当日已公布 LPR），再对每个目标日
        二分截取 (trade_date − 730 天, trade_date] 窗口统计百分位。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，index_valuation 需覆盖目标日前 730 天。

        Returns:
            key=交易日, value=FactorValue 的字典；历史样本不足 10 个时 numeric 为 None。
        """
        lpr = _LprLookup(ctx)
        valuation_dates, rows = ctx.valuation_for(index_code)
        erp_dates: list[date] = []
        erp_values: list[float] = []
        for row_date, row in zip(valuation_dates, rows):
            pe = _valid_pe(row)
            latest = lpr.as_of(row_date) if pe is not None else None
            if pe is None or latest is None:
                continue
            erp_dates.append(row_date)
            erp_values.append(1.0 / pe * 100.0 - latest[1])

        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            latest = lpr.as_of(trade_date)
            if latest is None:
                result[trade_date] = self._missing(index_code, "截止当日无有效 LPR 数据")
                continue
            end = bisect.bisect_right(erp_dates, trade_date)
            if end == 0 or erp_dates[end - 1] != trade_date:
                result[trade_date] = self._missing(index_code, "无当日有效 PE 数据")
                continue
            current_erp = erp_values[end - 1]
            window_start = trade_date - timedelta(days=_ERP_HISTORY_DAYS)
            start = bisect.bisect_left(erp_dates, window_start)
            history = erp_values[start:end]
            if len(history) < _ERP_MIN_HISTORY:
                result[trade_date] = self._missing(index_code, f"历史数据不足（{len(history)} 点）")
                continue
            count_below = sum(1 for value in history if value <= current_erp)
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(count_below / len(history) * 100, 2),
                payload={
                    "index_code": index_code,
                    "current_erp": round(current_erp, 4),
                    "history_count": len(history),
                    "history_start": str(erp_dates[start]),
                    "lpr_1y": latest[1],
                    "lpr_period": latest[0],
                },
            )
        return result

    def _missing(self, index_code: str, reason: str) -> FactorValue:
        """构造缺失值结果。"""
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=None,
            payload={"index_code": index_code, "reason": reason},
        )
