"""月线级别因子：从日线 OHLC 实时聚合月线数据，计算月线均线、动量和连续涨跌。

不新增 DB 表，直接从 FactorContext.index_bars 实时聚合月线 OHLC，
天然支持实时和回测两种模式，无额外数据同步问题。

策略用途：
- monthly_ma：牛熊状态判断（10 月均线方向）
- monthly_return：动量确认
- monthly_up_streak：熊市反弹确认（连续 2 月收阳）
"""

from __future__ import annotations

import math
from datetime import date
from typing import NamedTuple

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


def monthly_lookback_days(months: int) -> int:
    """按月数推导回望自然日数。

    月线由日线聚合，取得 months 个月线收盘价最多需要 months 个月的自然日；
    按每月 60 天并保留 1 年下限，兼容长假期与月内缺 bar 的指数。

    Args:
        months: 回望月数。

    Returns:
        回望自然日数，至少 365 天。
    """
    return max(365, int(months) * 60)


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


_OHLC_FIELDS = ("open_price", "high_price", "low_price", "close_price")


def _complete_daily_ohlc(
    index_code: str, ctx: FactorContext
) -> tuple[list[date], list[tuple[float, float, float, float]]]:
    """返回指定指数 OHLC 四价均有效（非 None、非 NaN）的有序日线（上下文内缓存）。

    Args:
        index_code: 指数代码。
        ctx: 因子上下文。

    Returns:
        (dates, [(open, high, low, close), ...])，按日期升序。
    """

    def _build() -> tuple[list[date], list[tuple[float, float, float, float]]]:
        bar_dates, values = ctx.series(index_code, _OHLC_FIELDS)
        kept_dates: list[date] = []
        kept: list[tuple[float, float, float, float]] = []
        for bar_date, ohlc in zip(bar_dates, values):
            if any(_is_price_missing(price) for price in ohlc):
                continue
            kept_dates.append(bar_date)
            kept.append(ohlc)
        return kept_dates, kept

    return ctx.memo(("monthly_ohlc", index_code), _build)


def _monthly_bars_by_date(
    index_code: str,
    dates: list[date],
    ctx: FactorContext,
    lookback_months: int,
) -> dict[date, list[MonthlyBar]]:
    """一次顺序扫描日线，为每个目标日期给出截至当日的最近 N 根月线。

    按年月分组，取每组首个交易日开盘价、最高/最低价、最后交易日收盘价。
    OHLC 四价任一缺失（None/NaN）的日线直接跳过，避免空 high/low 记为 0
    污染月线极值；若某月首个/末个交易日被跳过，则开盘/收盘取该月首个/末个
    数据完整的交易日（B14）。当月月线只聚合到目标日期为止（不使用未来日线）。

    Args:
        index_code: 指数代码。
        dates: 目标日期列表。
        ctx: 因子上下文。
        lookback_months: 每个日期返回的月数（含当月）。

    Returns:
        日期 → 按年月升序的月线列表（最多 lookback_months 根）。
    """
    bar_dates, values = _complete_daily_ohlc(index_code, ctx)
    completed: list[MonthlyBar] = []
    current_month: str | None = None
    current: list[float] = []
    position = 0
    result: dict[date, list[MonthlyBar]] = {}
    for trade_date in sorted(set(dates)):
        while position < len(bar_dates) and bar_dates[position] <= trade_date:
            bar_date = bar_dates[position]
            open_price, high_price, low_price, close_price = values[position]
            year_month = f"{bar_date.year:04d}-{bar_date.month:02d}"
            if year_month != current_month:
                if current_month is not None:
                    completed.append(MonthlyBar(current_month, *current))
                current_month = year_month
                current = [open_price, high_price, low_price, close_price]
            else:
                current[1] = max(current[1], high_price)
                current[2] = min(current[2], low_price)
                current[3] = close_price
            position += 1
        if current_month is None:
            result[trade_date] = []
            continue
        prior = completed[-(lookback_months - 1) :] if lookback_months > 1 else []
        result[trade_date] = [*prior, MonthlyBar(current_month, *current)]
    return result


def _aggregate_monthly_bars(
    index_code: str,
    trade_date: date,
    ctx: FactorContext,
    lookback_months: int = 13,
) -> list[MonthlyBar]:
    """从日线 OHLC 聚合截至 trade_date 的最近 lookback_months 根月线。

    Args:
        index_code: 指数代码。
        trade_date: 目标交易日。
        ctx: 因子上下文，包含日线数据。
        lookback_months: 向前回溯的月数（含当月），默认 13。

    Returns:
        月线 OHLC 列表，按年月升序排列。
    """
    return _monthly_bars_by_date(index_code, [trade_date], ctx, lookback_months)[trade_date]


# ══════════════════════════════════════════════════════════════════════
# 月线均线因子
# ══════════════════════════════════════════════════════════════════════


class MonthlyMAComputer:
    """月线均线因子计算器。

    计算指定月数的简单移动平均（SMA），从日线实时聚合月线数据（当月月线
    聚合到目标日为止）。用于沪深300波段策略的牛熊状态判断（10 月均线方向）。

    Attributes:
        _months: 均线周期（月数）。
    """

    def __init__(self, months: int = 10) -> None:
        """初始化月线均线计算器。

        Args:
            months: 均线周期（月数），如 5 或 10。

        Raises:
            ValueError: months 小于 2，单月均线等价于当月收盘价。
        """
        if months < 2:
            raise ValueError("months 必须至少为 2")
        self._months = int(months)

    @property
    def spec(self) -> FactorSpec:
        """返回月线均线的因子元数据。"""
        return FactorSpec(
            factor_id=f"monthly_ma_{self._months}m",
            name=f"{self._months}月均线",
            category="technical",
            version="1.0.0",
            description=f"指数近 {self._months} 个月线收盘价的简单移动平均，从日线实时聚合。",
            required_data=["index_bars"],
            lookback_days=monthly_lookback_days(self._months),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算月线均线（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的月线均线。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: 因子上下文。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        snapshots = _monthly_bars_by_date(index_code, dates, ctx, self._months + 1)
        return {trade_date: self._build_value(snapshots[trade_date]) for trade_date in dates}

    def _build_value(self, monthly_bars: list[MonthlyBar]) -> FactorValue:
        """由月线序列构造月线均线因子值。"""
        if len(monthly_bars) < self._months:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "reason": f"月线数据不足 {self._months} 个月",
                    "available_months": len(monthly_bars),
                },
            )
        closes = [bar.close for bar in monthly_bars[-self._months :]]
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(sum(closes) / len(closes), 4),
            payload={"months": self._months, "sample_count": len(closes)},
        )


# ══════════════════════════════════════════════════════════════════════
# 月线动量因子
# ══════════════════════════════════════════════════════════════════════


class MonthlyReturnComputer:
    """月线动量因子计算器。

    计算近 N 个月的收益率（%），基于月线收盘价（当月取目标日收盘）。
    用于沪深300波段策略的动量确认（近 2 月/3 月收益率）。

    Attributes:
        _months: 回望月数。
    """

    def __init__(self, months: int = 2) -> None:
        """初始化月线动量计算器。

        Args:
            months: 回望月数，如 2 或 3。

        Raises:
            ValueError: months 小于 1。
        """
        if months < 1:
            raise ValueError("months 必须不小于 1")
        self._months = int(months)

    @property
    def spec(self) -> FactorSpec:
        """返回月线动量的因子元数据。"""
        return FactorSpec(
            factor_id=f"monthly_return_{self._months}m",
            name=f"近{self._months}月收益率",
            category="momentum",
            version="1.0.0",
            description=f"指数近 {self._months} 个月的收益率（%），基于月线收盘价。",
            required_data=["index_bars"],
            lookback_days=monthly_lookback_days(self._months),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算月线动量（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的月线动量。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: 因子上下文。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        snapshots = _monthly_bars_by_date(index_code, dates, ctx, self._months + 1)
        return {trade_date: self._build_value(snapshots[trade_date]) for trade_date in dates}

    def _build_value(self, monthly_bars: list[MonthlyBar]) -> FactorValue:
        """由月线序列构造月线动量因子值。"""
        if len(monthly_bars) < self._months + 1:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "reason": f"月线数据不足 {self._months + 1} 个月",
                    "available_months": len(monthly_bars),
                },
            )
        current_close = monthly_bars[-1].close
        base_close = monthly_bars[-(self._months + 1)].close
        if base_close <= 0:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "基准月收盘价异常"},
            )
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round((current_close / base_close - 1) * 100, 4),
            payload={
                "months": self._months,
                "current_close": round(current_close, 2),
                "base_close": round(base_close, 2),
            },
        )


# ══════════════════════════════════════════════════════════════════════
# 连续收阳/收阴因子
# ══════════════════════════════════════════════════════════════════════


class MonthlyStreakComputer:
    """连续收阳/收阴月数因子计算器。

    统计截至 trade_date 所在月（含未走完的当月）的连续同方向月数。
    正值表示连续收阳月数，负值表示连续收阴月数，0 表示最近一月收平。
    月线不足 2 个月时返回 None（与"收平"区分）。
    用于沪深300波段策略的熊市反弹确认（连续2月收阳）。

    示例：
        +2 = 连续 2 个月收阳（close > open）
        -3 = 连续 3 个月收阴（close < open）
        0 = 最近一月收平
    """

    @property
    def spec(self) -> FactorSpec:
        """返回连续收阳/收阴因子的元数据。"""
        return FactorSpec(
            factor_id="monthly_up_streak",
            name="连续收阳月数",
            category="momentum",
            version="1.1.0",
            description=(
                "截至当月的连续收阳/收阴月数。"
                "正值=连续收阳，负值=连续收阴，0=最近一月收平；月线不足 2 个月时为空。"
                "用于熊市反弹确认（连续2月收阳）。"
            ),
            required_data=["index_bars"],
            lookback_days=365,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算连续收阳/收阴月数（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的连续收阳/收阴月数。

        Args:
            index_code: 指数代码。
            dates: 需要计算的交易日列表（升序）。
            ctx: 因子上下文。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        snapshots = _monthly_bars_by_date(index_code, dates, ctx, 13)
        return {trade_date: self._build_value(snapshots[trade_date]) for trade_date in dates}

    def _build_value(self, monthly_bars: list[MonthlyBar]) -> FactorValue:
        """由月线序列构造连续收阳/收阴因子值。"""
        if len(monthly_bars) < 2:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "月线数据不足 2 个月", "available_months": len(monthly_bars)},
            )

        # 从最近一月（含未走完的当月）向前统计同方向月数，收平即中断
        streak = 0
        direction = 0  # 1=收阳, -1=收阴
        for bar in reversed(monthly_bars):
            bar_direction = 1 if bar.close > bar.open else -1 if bar.close < bar.open else 0
            if bar_direction == 0 or (direction != 0 and bar_direction != direction):
                break
            direction = bar_direction
            streak += bar_direction

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=float(streak),
            payload={
                "streak": streak,
                "direction": "up" if streak > 0 else "down" if streak < 0 else "flat",
            },
        )
