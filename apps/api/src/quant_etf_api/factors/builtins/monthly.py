"""月线级别因子：从日线 OHLC 实时聚合月线数据，计算月线均线、动量和连续涨跌。

不新增 DB 表，直接从 FactorContext.index_bars 实时聚合月线 OHLC，
天然支持实时和回测两种模式，无额外数据同步问题。

策略用途：
- monthly_ma_5m / monthly_ma_10m：牛熊状态判断（10月均线方向）
- monthly_return_2m / monthly_return_3m：动量确认
- monthly_up_streak：熊市反弹确认（连续2月收阳）
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import NamedTuple

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


class MonthlyBar(NamedTuple):
    """月线 OHLC 数据。"""

    year_month: str  # 格式 YYYY-MM
    open: float
    high: float
    low: float
    close: float


def _is_price_missing(value: float | None) -> bool:
    """判断价格字段是否缺失（None 或 NaN）。

    PostgreSQL float 列允许存储 NaN，NaN 参与 max/min 会污染月线极值，
    统一按"缺失"处理，与 B10 缺口语义一致。

    Args:
        value: 价格或 None。

    Returns:
        value 为 None 或 float 且为 NaN 时返回 True。
    """
    return value is None or (isinstance(value, float) and math.isnan(value))


def _aggregate_monthly_bars(
    index_code: str,
    trade_date: date,
    ctx: FactorContext,
    lookback_months: int = 13,
) -> list[MonthlyBar]:
    """从日线 OHLC 聚合月线 OHLC 数据。

    按年月分组，取每组首个交易日开盘价、最高/最低价、最后交易日收盘价。
    OHLC 四价任一缺失（None/NaN）的日线直接跳过，避免空 high/low 记为 0
    污染月线极值；若某月首个/末个交易日被跳过，则开盘/收盘取该月首个/末个
    数据完整的交易日（B14）。
    只返回 trade_date 所在月及之前 lookback_months 个月的数据。

    Args:
        index_code: 指数代码。
        trade_date: 目标交易日。
        ctx: 因子上下文，包含日线数据。
        lookback_months: 向前回溯的月数（含当月），默认 13。

    Returns:
        月线 OHLC 列表，按年月升序排列。
    """
    # 收集 trade_date 及之前的日线数据
    bars_by_month: dict[str, list[tuple[date, float, float, float, float]]] = defaultdict(list)
    cutoff_year_month = f"{trade_date.year:04d}-{trade_date.month:02d}"

    for (code, dt), bar in ctx.index_bars.items():
        if code != index_code or dt > trade_date:
            continue
        if (
            _is_price_missing(bar.open_price)
            or _is_price_missing(bar.high_price)
            or _is_price_missing(bar.low_price)
            or _is_price_missing(bar.close_price)
        ):
            continue
        ym = f"{dt.year:04d}-{dt.month:02d}"
        bars_by_month[ym].append(
            (dt, bar.open_price, bar.high_price, bar.low_price, bar.close_price)
        )

    if not bars_by_month:
        return []

    # 按年月排序，取最近 lookback_months 个月
    sorted_months = sorted(bars_by_month.keys())
    # 只保留 cutoff_year_month 及之前的月份
    sorted_months = [m for m in sorted_months if m <= cutoff_year_month]
    sorted_months = sorted_months[-lookback_months:]

    result: list[MonthlyBar] = []
    for ym in sorted_months:
        month_bars = sorted(bars_by_month[ym], key=lambda x: x[0])
        if not month_bars:
            continue
        open_price = month_bars[0][1]  # 首个交易日开盘价
        high_price = max(b[2] for b in month_bars)  # 最高价
        low_price = min(b[3] for b in month_bars)  # 最低价
        close_price = month_bars[-1][4]  # 最后交易日收盘价
        result.append(
            MonthlyBar(
                year_month=ym, open=open_price, high=high_price, low=low_price, close=close_price
            )
        )

    return result


# ══════════════════════════════════════════════════════════════════════
# 月线均线因子
# ══════════════════════════════════════════════════════════════════════


class MonthlyMAComputer:
    """月线均线因子计算器。

    计算指定月数的简单移动平均（SMA），从日线实时聚合月线数据。
    用于沪深300波段策略的牛熊状态判断（10月均线方向）。

    Attributes:
        _period: 均线周期（月数）。
    """

    def __init__(self, period: int = 10) -> None:
        """初始化月线均线计算器。

        Args:
            period: 均线周期（月数），如 5 或 10。
        """
        self._period = period

    @property
    def spec(self) -> FactorSpec:
        """返回月线均线的因子元数据。"""
        return FactorSpec(
            factor_id=f"monthly_ma_{self._period}m",
            name=f"{self._period}月均线",
            category="technical",
            version="1.0.0",
            description=f"指数近 {self._period} 个月线收盘价的简单移动平均，从日线实时聚合。",
            required_data=["index_bars"],
            lookback_days=max(365, self._period * 30 * 2),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算月线均线。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: 因子上下文。

        Returns:
            FactorValue，月线数据不足时 numeric 为 None。
        """
        monthly_bars = _aggregate_monthly_bars(
            index_code, trade_date, ctx, lookback_months=self._period + 1
        )
        if len(monthly_bars) < self._period:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "reason": f"月线数据不足 {self._period} 个月",
                    "available_months": len(monthly_bars),
                },
            )

        closes = [bar.close for bar in monthly_bars[-self._period :]]
        ma = round(sum(closes) / len(closes), 4)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=ma,
            payload={"period_months": self._period, "sample_count": len(closes)},
        )


# ══════════════════════════════════════════════════════════════════════
# 月线动量因子
# ══════════════════════════════════════════════════════════════════════


class MonthlyReturnComputer:
    """月线动量因子计算器。

    计算近 N 个月的收益率（%），基于月线收盘价。
    用于沪深300波段策略的动量确认（近2月/3月收益率）。

    Attributes:
        _period: 回望月数。
    """

    def __init__(self, period: int = 2) -> None:
        """初始化月线动量计算器。

        Args:
            period: 回望月数，如 2 或 3。
        """
        self._period = period

    @property
    def spec(self) -> FactorSpec:
        """返回月线动量的因子元数据。"""
        return FactorSpec(
            factor_id=f"monthly_return_{self._period}m",
            name=f"近{self._period}月收益率",
            category="momentum",
            version="1.0.0",
            description=f"指数近 {self._period} 个月的收益率（%），基于月线收盘价。",
            required_data=["index_bars"],
            lookback_days=max(180, self._period * 30 * 2),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算月线动量。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: 因子上下文。

        Returns:
            FactorValue，月线数据不足时 numeric 为 None。
        """
        monthly_bars = _aggregate_monthly_bars(
            index_code, trade_date, ctx, lookback_months=self._period + 1
        )
        if len(monthly_bars) < self._period + 1:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "reason": f"月线数据不足 {self._period + 1} 个月",
                    "available_months": len(monthly_bars),
                },
            )

        current_close = monthly_bars[-1].close
        base_close = monthly_bars[-(self._period + 1)].close
        if base_close <= 0:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "基准月收盘价异常"},
            )

        ret = round((current_close / base_close - 1) * 100, 4)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=ret,
            payload={
                "period_months": self._period,
                "current_close": round(current_close, 2),
                "base_close": round(base_close, 2),
            },
        )


# ══════════════════════════════════════════════════════════════════════
# 连续收阳/收阴因子
# ══════════════════════════════════════════════════════════════════════


class MonthlyStreakComputer:
    """连续收阳/收阴月数因子计算器。

    统计截至 trade_date 所在月的连续同方向月数。
    正值表示连续收阳月数，负值表示连续收阴月数，0 表示本月无涨跌。
    用于沪深300波段策略的熊市反弹确认（连续2月收阳）。

    示例：
        +2 = 连续 2 个月收阳（close > open）
        -3 = 连续 3 个月收阴（close < open）
        0 = 当月收平或数据不足
    """

    @property
    def spec(self) -> FactorSpec:
        """返回连续收阳/收阴因子的元数据。"""
        return FactorSpec(
            factor_id="monthly_up_streak",
            name="连续收阳月数",
            category="momentum",
            version="1.0.0",
            description=(
                "截至当月的连续收阳/收阴月数。"
                "正值=连续收阳，负值=连续收阴，0=收平或数据不足。"
                "用于熊市反弹确认（连续2月收阳）。"
            ),
            required_data=["index_bars"],
            lookback_days=365,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算连续收阳/收阴月数。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: 因子上下文。

        Returns:
            FactorValue，月线数据不足时 numeric 为 0。
        """
        monthly_bars = _aggregate_monthly_bars(index_code, trade_date, ctx, lookback_months=13)
        if len(monthly_bars) < 2:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=0,
                payload={"reason": "月线数据不足", "available_months": len(monthly_bars)},
            )

        # 从最后一月（不含当月，因当月可能未结束）向前遍历
        # 使用倒数第二月作为起始（当月可能数据不完整）
        # 但策略文档要求"连续2个月成立"，所以包含当月
        streak = 0
        direction = 0  # 1=收阳, -1=收阴

        for bar in reversed(monthly_bars):
            if bar.close > bar.open:
                if direction == 0:
                    direction = 1
                    streak = 1
                elif direction == 1:
                    streak += 1
                else:
                    break
            elif bar.close < bar.open:
                if direction == 0:
                    direction = -1
                    streak = -1
                elif direction == -1:
                    streak -= 1
                else:
                    break
            else:
                # 收平，中断连续
                break

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=float(streak),
            payload={
                "streak": streak,
                "direction": "up" if streak > 0 else "down" if streak < 0 else "flat",
            },
        )
