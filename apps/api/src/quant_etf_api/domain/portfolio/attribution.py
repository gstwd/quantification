"""回测收益归因（纯领域逻辑，读取路径现算，不重跑回测）。

逐日恒等式（收益为 %，权重为小数）：

    r_p = Σ w_k · r_k + 残差
    r_p − r_b = (E − 1) · r_b  +  Σ w_k · (r_k − r_b)  +  残差
              =   择时（仓位）  +       选择（成分）       +  残差

其中 w_k 为当日承担收益的实际持仓（``executed_positions``），E = Σ w_k，r_k 为该资产收盘到
收盘的收益（``backtest_index_result.index_return``），r_b 为基准收益。T+1 收盘执行（研究
缺省）下组合收益正是 Σ w_k · r_k，残差只来自权重 4 位取整；T+1 开盘执行的调仓日组合收益
按隔夜 + 日内两段复利，收盘到收盘的分解有残差，结果标注为近似。

多日汇总用逐日贡献的算术和（不做复利链接），与累计收益的差异同样体现在残差里。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class AttributionDay:
    """归因所需的单日输入。

    Attributes:
        trade_date: 决策日（收益窗口 [T, T+1]）。
        portfolio_return: 组合收益（%）。
        positions: 当日承担收益的实际持仓。
        benchmark_return: 基准收益（%），未启用基准为 None。
    """

    trade_date: date
    portfolio_return: float
    positions: Mapping[str, float]
    benchmark_return: float | None = None


@dataclass
class AssetContribution:
    """单个资产的贡献汇总。

    Attributes:
        index_code: 指数代码。
        contribution_pct: Σ w · r（百分点）。
        held_days: 持有天数。
        average_weight: 持有日平均权重。
        win_rate: 持有日资产收益为正的占比。
        missing_days: 持有但缺少资产收益的天数。
    """

    index_code: str
    contribution_pct: float = 0.0
    held_days: int = 0
    average_weight: float = 0.0
    win_rate: float = 0.0
    missing_days: int = 0
    _weight_sum: float = field(default=0.0, repr=False)
    _wins: int = field(default=0, repr=False)


@dataclass
class AttributionBucket:
    """一段区间（全区间或单个年度）的分解汇总（百分点，算术和）。

    Attributes:
        label: 区间标签（"全区间" 或年份）。
        days: 交易日数。
        portfolio_pct: Σ 组合收益。
        benchmark_pct: Σ 基准收益（无基准时为 None）。
        timing_pct: Σ (E − 1) · r_b（无基准时为 None）。
        selection_pct: Σ w · (r − r_b)（无基准时为 None）。
        explained_pct: Σ w · r。
        residual_pct: Σ (r_p − Σ w · r)。
    """

    label: str
    days: int = 0
    portfolio_pct: float = 0.0
    benchmark_pct: float | None = None
    timing_pct: float | None = None
    selection_pct: float | None = None
    explained_pct: float = 0.0
    residual_pct: float = 0.0


@dataclass
class AttributionResult:
    """归因结果。

    Attributes:
        total: 全区间汇总。
        yearly: 分年度汇总。
        assets: 资产贡献（按贡献降序）。
        approximate: 分解是否为近似（开盘执行模型）。
    """

    total: AttributionBucket
    yearly: list[AttributionBucket]
    assets: list[AssetContribution]
    approximate: bool


def _add(bucket: AttributionBucket, day: AttributionDay, explained: float, exposure: float) -> None:
    """把一天的分解累加进区间汇总。"""
    bucket.days += 1
    bucket.portfolio_pct += day.portfolio_return
    bucket.explained_pct += explained
    bucket.residual_pct += day.portfolio_return - explained
    if day.benchmark_return is None:
        return
    bench = day.benchmark_return
    bucket.benchmark_pct = (bucket.benchmark_pct or 0.0) + bench
    bucket.timing_pct = (bucket.timing_pct or 0.0) + (exposure - 1.0) * bench
    bucket.selection_pct = (bucket.selection_pct or 0.0) + explained - exposure * bench


def compute_attribution(
    days: Iterable[AttributionDay],
    asset_returns: Mapping[tuple[date, str], float | None],
    *,
    approximate: bool = False,
) -> AttributionResult:
    """逐日分解并汇总。

    Args:
        days: 按日期升序的逐日输入。
        asset_returns: (决策日, 指数代码) → 资产收益（%），缺失为 None 或不出现。
        approximate: 是否标注为近似（开盘执行模型）。

    Returns:
        AttributionResult；无基准的日子只计入组合与资产贡献。
    """
    total = AttributionBucket(label="全区间")
    yearly: dict[int, AttributionBucket] = {}
    assets: dict[str, AssetContribution] = {}
    for day in days:
        explained = 0.0
        exposure = 0.0
        for code, weight in day.positions.items():
            if weight <= 0:
                continue
            exposure += weight
            item = assets.setdefault(code, AssetContribution(index_code=code))
            item.held_days += 1
            item._weight_sum += weight
            ret = asset_returns.get((day.trade_date, code))
            if ret is None:
                item.missing_days += 1
                continue
            contribution = weight * ret
            explained += contribution
            item.contribution_pct += contribution
            if ret > 0:
                item._wins += 1
        _add(total, day, explained, exposure)
        bucket = yearly.setdefault(
            day.trade_date.year, AttributionBucket(label=str(day.trade_date.year))
        )
        _add(bucket, day, explained, exposure)
    for item in assets.values():
        item.average_weight = item._weight_sum / item.held_days if item.held_days else 0.0
        valid = item.held_days - item.missing_days
        item.win_rate = item._wins / valid if valid > 0 else 0.0
    ordered = sorted(assets.values(), key=lambda a: a.contribution_pct, reverse=True)
    return AttributionResult(
        total=total,
        yearly=[yearly[year] for year in sorted(yearly)],
        assets=ordered,
        approximate=approximate,
    )
