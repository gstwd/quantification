"""择时模块可观测性的纯函数：回答"择时在回测里实际做了什么"。

回测逐日只记录 ``timing_regime``，汇总指标里没有任何择时专项度量，无法判断
择时究竟贡献了收益/回撤改善，还是只增加了换手。本模块从主循环收集的逐日记录
计算：

- 各 regime 时间占比与 regime 翻转频率（逐日信号口径）；
- 风险腿到期日 regime / 目标仓位的实际切换次数（执行口径，周度风险腿只在
  到期日把 regime 落到持仓上）；
- 分 regime 的组合日收益（以及同期基准日收益，用来看 regime 是否真的区分了
  市场好坏）；
- 风险腿成交日换手占总换手的比例，以及总仓位变动带来的换手下限占比；
- 择时综合分的分布（分位数）。

全部函数为纯函数，不读写数据库；日收益单位为百分数（1.0 表示 1%），年化系数
默认 252，与 ``domain.research.metrics`` / ``stability`` 口径一致。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from quant_etf_api.domain.research.metrics import calc_annualized_return_pct

# 年化系数与绩效指标模块保持一致
DEFAULT_TRADING_DAYS_PER_YEAR = 252
# 引擎可输出的 regime（与 engine.config.RegimeName 一致），按仓位从高到低排列
TIMING_REGIMES: tuple[str, ...] = ("offensive", "neutral", "defensive")
# 择时综合分分布输出的分位点
TIMING_SCORE_QUANTILES: tuple[float, ...] = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)


@dataclass(frozen=True)
class TimingDay:
    """单个决策日的择时观测记录（与回测逐日结果一一对应）。

    Attributes:
        regime: 当日引擎输出的 regime；未配置择时时为 None。
        score: 当日择时综合分；择时因子全部缺失（降级）时为 None。
        degraded: 当日择时是否降级（因子全部缺失、按中性运行）。
        portfolio_return: 当日组合收益（%，收益窗口 [收盘 T, 收盘 T+1]）。
        benchmark_return: 同窗口基准收益（%），未启用基准时为 None。
        exposure: 当日组合总仓位（0-1）。
        target_exposure: 当日择时目标仓位（regime 仓位经组合级风控裁剪），
            未配置择时时为 None。
        turnover: 当日成交的单边换手率（成交日记账，见 ``fill_lag``）。
        exposure_turnover: 当日成交中总仓位变动带来的单边换手 |ΣW新 − ΣW旧| / 2。
        risk_order: 当日风险腿到期（下达按择时缩放总仓位的指令）。
        selection_order: 当日选股腿到期（下达重建成分的指令）。
    """

    regime: str | None
    score: float | None
    degraded: bool
    portfolio_return: float
    benchmark_return: float | None = None
    exposure: float | None = None
    target_exposure: float | None = None
    turnover: float = 0.0
    exposure_turnover: float = 0.0
    risk_order: bool = False
    selection_order: bool = False


@dataclass(frozen=True)
class RegimeReturnStats:
    """单个 regime 下的时间占比与收益统计（按决策日 regime 归组）。

    Attributes:
        regime: regime 名称。
        days: 该 regime 的决策日数。
        share: 时间占比（0-1，分母为有 regime 的决策日数）。
        average_exposure: 该 regime 决策日的平均组合仓位（0-1）。
        mean_daily_return_pct: 组合日收益均值（%）。
        annualized_return_pct: 组合在该 regime 日子里的几何年化收益（%）。
        benchmark_mean_daily_return_pct: 同期基准日收益均值（%）。
        benchmark_annualized_return_pct: 同期基准几何年化收益（%）。
    """

    regime: str
    days: int
    share: float
    average_exposure: float | None = None
    mean_daily_return_pct: float | None = None
    annualized_return_pct: float | None = None
    benchmark_mean_daily_return_pct: float | None = None
    benchmark_annualized_return_pct: float | None = None


@dataclass(frozen=True)
class ScoreQuantile:
    """择时综合分的一个分位点。

    Attributes:
        quantile: 分位（0-1）。
        value: 该分位的综合分。
    """

    quantile: float
    value: float


@dataclass(frozen=True)
class ScoreDistribution:
    """择时综合分分布。

    Attributes:
        count: 有效综合分个数（降级日不计）。
        mean: 均值。
        std: 标准差（样本数 < 2 时为 None）。
        min: 最小值。
        max: 最大值。
        quantiles: 分位点列表（见 ``TIMING_SCORE_QUANTILES``）。
    """

    count: int
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    quantiles: list[ScoreQuantile] = field(default_factory=list)


@dataclass(frozen=True)
class TurnoverBreakdown:
    """换手按调仓腿拆分。

    Attributes:
        total_turnover: 全期单边换手合计（倍）。
        annualized_turnover: 年化单边换手（倍）。
        risk_leg_turnover: 风险腿指令成交日的换手合计（含两腿同日到期）。
        risk_leg_turnover_share: 风险腿成交日换手 / 总换手；总换手为 0 时为 None。
        risk_only_turnover_share: 仅风险腿到期（选股腿未到期）的成交日换手 / 总换手。
        exposure_turnover: 总仓位变动带来的换手合计（Σ|ΔΣW| / 2，择时换手的下限估计）。
        exposure_turnover_share: 总仓位变动换手 / 总换手。
    """

    total_turnover: float
    annualized_turnover: float
    risk_leg_turnover: float
    risk_leg_turnover_share: float | None
    risk_only_turnover_share: float | None
    exposure_turnover: float
    exposure_turnover_share: float | None


@dataclass(frozen=True)
class TimingStats:
    """回测择时专项统计。

    Attributes:
        trading_days: 决策日数（= 逐日结果行数）。
        timing_days: 有 regime 的决策日数。
        degraded_days: 择时降级（因子全部缺失、按中性运行）的决策日数。
        regime_flips: 逐日 regime 切换次数（信号口径）。
        regime_flips_per_year: 每年 regime 切换次数。
        risk_leg_days: 风险腿到期的决策日数。
        risk_leg_regime_changes: 相邻两个风险腿到期日 regime 不同的次数（执行口径）。
        risk_leg_regime_changes_per_year: 每年执行口径 regime 切换次数。
        target_exposure_switches: 相邻两个风险腿到期日择时目标仓位不同的次数
            （regime 切换但仓位相同时不计，反映择时实际改动仓位的频率）。
        target_exposure_switches_per_year: 每年目标仓位切换次数。
        regimes: 分 regime 统计（按 offensive / neutral / defensive 顺序）。
        turnover: 换手拆分。
        score: 择时综合分分布。
        offensive_threshold: 进攻阈值（综合分 ≥ 该值为 offensive），未知时为 None。
        defensive_threshold: 防守阈值（综合分 ≤ 该值为 defensive），未知时为 None。
    """

    trading_days: int
    timing_days: int
    degraded_days: int
    regime_flips: int
    regime_flips_per_year: float | None
    risk_leg_days: int
    risk_leg_regime_changes: int
    risk_leg_regime_changes_per_year: float | None
    target_exposure_switches: int
    target_exposure_switches_per_year: float | None
    regimes: list[RegimeReturnStats]
    turnover: TurnoverBreakdown
    score: ScoreDistribution
    offensive_threshold: float | None = None
    defensive_threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入 JSON 列的字典（嵌套数据类一并展开）。"""
        return asdict(self)


def _round(value: float | None, digits: int = 4) -> float | None:
    """取整并把 NaN/Inf 转为 None（结果要写入 JSON 列）。"""
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _per_year(count: int, days: int, trading_days_per_year: int) -> float | None:
    """把区间内的次数折算为每年次数（无交易日时为 None）。"""
    if days <= 0 or trading_days_per_year <= 0:
        return None
    return _round(count / (days / trading_days_per_year), 2)


def count_regime_flips(regimes: Sequence[str | None]) -> int:
    """统计相邻有效 regime 不同的次数。

    缺失（None）不算切换，也不打断比较：降级日前后的 regime 直接相比，
    避免一次降级被算成两次翻转。

    Args:
        regimes: 按日期升序的 regime 序列。

    Returns:
        切换次数。
    """
    flips = 0
    previous: str | None = None
    for regime in regimes:
        if regime is None:
            continue
        if previous is not None and regime != previous:
            flips += 1
        previous = regime
    return flips


def regime_shares(regimes: Sequence[str | None]) -> dict[str, float]:
    """计算各 regime 的时间占比（分母为有 regime 的天数）。

    Args:
        regimes: regime 序列。

    Returns:
        {regime: 占比}，按 ``TIMING_REGIMES`` 顺序列出全部 regime（未出现记 0）；
        没有任何有效 regime 时返回空字典。
    """
    valid = [regime for regime in regimes if regime is not None]
    if not valid:
        return {}
    return {regime: round(valid.count(regime) / len(valid), 4) for regime in TIMING_REGIMES}


def score_distribution(
    scores: Sequence[float | None],
    quantiles: Sequence[float] = TIMING_SCORE_QUANTILES,
) -> ScoreDistribution:
    """计算择时综合分分布（线性插值分位，与 numpy 默认口径一致）。

    Args:
        scores: 综合分序列，None / 非有限值不计。
        quantiles: 输出的分位点（0-1）。

    Returns:
        ScoreDistribution；没有有效值时只有 count=0。
    """
    values = sorted(float(v) for v in scores if v is not None and math.isfinite(float(v)))
    if not values:
        return ScoreDistribution(count=0)
    n = len(values)
    mean = sum(values) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else None

    def _quantile(q: float) -> float:
        """线性插值分位。"""
        position = (n - 1) * q
        lower = math.floor(position)
        upper = min(lower + 1, n - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    return ScoreDistribution(
        count=n,
        mean=_round(mean, 2),
        std=_round(std, 2) if std is not None else None,
        min=_round(values[0], 2),
        max=_round(values[-1], 2),
        quantiles=[ScoreQuantile(quantile=q, value=round(_quantile(q), 2)) for q in quantiles],
    )


def regime_return_stats(
    days: Sequence[TimingDay],
    trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
) -> list[RegimeReturnStats]:
    """按决策日 regime 归组统计组合与基准的日收益。

    归组口径：第 i 行的收益窗口是 [收盘 T_i, 收盘 T_{i+1}]，按 T_i 的 regime 归组，
    即"发出该 regime 信号之后的下一日收益"。周度风险腿下 regime 不一定当日落到
    持仓上，因此同时给出该组的平均仓位，用来区分"信号没区分市场"与"信号区分了
    但仓位没跟上"。

    Args:
        days: 逐日择时记录。
        trading_days_per_year: 年化系数。

    Returns:
        按 ``TIMING_REGIMES`` 顺序的统计（未出现的 regime 也列出，days=0）。
    """
    timed = [day for day in days if day.regime is not None]
    total = len(timed)
    result: list[RegimeReturnStats] = []
    for regime in TIMING_REGIMES:
        group = [day for day in timed if day.regime == regime]
        if not group:
            result.append(RegimeReturnStats(regime=regime, days=0, share=0.0))
            continue
        returns = [day.portfolio_return for day in group]
        exposures = [day.exposure for day in group if day.exposure is not None]
        bench = [day.benchmark_return for day in group if day.benchmark_return is not None]
        result.append(
            RegimeReturnStats(
                regime=regime,
                days=len(group),
                share=round(len(group) / total, 4) if total else 0.0,
                average_exposure=_round(sum(exposures) / len(exposures)) if exposures else None,
                mean_daily_return_pct=_round(sum(returns) / len(returns)),
                annualized_return_pct=_round(
                    calc_annualized_return_pct(returns, trading_days_per_year), 2
                ),
                benchmark_mean_daily_return_pct=(
                    _round(sum(bench) / len(bench)) if bench else None
                ),
                benchmark_annualized_return_pct=(
                    _round(calc_annualized_return_pct(bench, trading_days_per_year), 2)
                    if bench
                    else None
                ),
            )
        )
    return result


def turnover_breakdown(
    days: Sequence[TimingDay],
    fill_lag: int = 0,
    trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
) -> TurnoverBreakdown:
    """把换手按调仓腿拆分。

    换手记在成交行：T+1 开盘执行时第 i 行决策当行成交（``fill_lag=0``）；T+1 收盘
    执行时第 i 行的指令在第 i+1 行成交（``fill_lag=1``）。因此第 j 行的换手对应第
    j − fill_lag 行下达的指令。

    Args:
        days: 逐日择时记录。
        fill_lag: 指令到成交的行滞后（0 或 1）。
        trading_days_per_year: 年化系数。

    Returns:
        TurnoverBreakdown。
    """
    total = 0.0
    risk_leg = 0.0
    risk_only = 0.0
    exposure = 0.0
    for index, day in enumerate(days):
        turnover = max(day.turnover, 0.0)
        total += turnover
        exposure += max(day.exposure_turnover, 0.0)
        order_index = index - fill_lag
        if order_index < 0:
            continue
        order = days[order_index]
        if order.risk_order:
            risk_leg += turnover
            if not order.selection_order:
                risk_only += turnover
    years = len(days) / trading_days_per_year if trading_days_per_year > 0 else 0.0

    def _share(part: float) -> float | None:
        """占总换手比例（总换手为 0 时无意义）。"""
        return round(part / total, 4) if total > 0 else None

    return TurnoverBreakdown(
        total_turnover=round(total, 4),
        annualized_turnover=round(total / years, 4) if years > 0 else 0.0,
        risk_leg_turnover=round(risk_leg, 4),
        risk_leg_turnover_share=_share(risk_leg),
        risk_only_turnover_share=_share(risk_only),
        exposure_turnover=round(exposure, 4),
        exposure_turnover_share=_share(exposure),
    )


def _risk_leg_changes(values: Sequence[Any]) -> int:
    """统计相邻风险腿到期日取值不同的次数（None 不参与比较）。"""
    changes = 0
    previous: Any = None
    for value in values:
        if value is None:
            continue
        if previous is not None and value != previous:
            changes += 1
        previous = value
    return changes


def compute_timing_stats(
    days: Sequence[TimingDay],
    *,
    fill_lag: int = 0,
    offensive_threshold: float | None = None,
    defensive_threshold: float | None = None,
    trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
) -> TimingStats | None:
    """汇总回测的择时专项统计。

    Args:
        days: 逐日择时记录（按日期升序，与回测逐日结果等长）。
        fill_lag: 指令到成交的行滞后（T+1 开盘 0，T+1 收盘 1）。
        offensive_threshold: 进攻阈值，仅随结果透传便于解读分布。
        defensive_threshold: 防守阈值，仅随结果透传便于解读分布。
        trading_days_per_year: 年化系数。

    Returns:
        TimingStats；没有任何决策日带 regime（未配置择时）时返回 None。
    """
    if not days or all(day.regime is None for day in days):
        return None
    n = len(days)
    regimes = [day.regime for day in days]
    flips = count_regime_flips(regimes)
    risk_days = [day for day in days if day.risk_order]
    regime_changes = _risk_leg_changes([day.regime for day in risk_days])
    exposure_switches = _risk_leg_changes(
        [
            round(day.target_exposure, 4) if day.target_exposure is not None else None
            for day in risk_days
        ]
    )
    return TimingStats(
        trading_days=n,
        timing_days=sum(1 for regime in regimes if regime is not None),
        degraded_days=sum(1 for day in days if day.degraded),
        regime_flips=flips,
        regime_flips_per_year=_per_year(flips, n, trading_days_per_year),
        risk_leg_days=len(risk_days),
        risk_leg_regime_changes=regime_changes,
        risk_leg_regime_changes_per_year=_per_year(regime_changes, n, trading_days_per_year),
        target_exposure_switches=exposure_switches,
        target_exposure_switches_per_year=_per_year(exposure_switches, n, trading_days_per_year),
        regimes=regime_return_stats(days, trading_days_per_year),
        turnover=turnover_breakdown(days, fill_lag, trading_days_per_year),
        score=score_distribution([day.score for day in days]),
        offensive_threshold=offensive_threshold,
        defensive_threshold=defensive_threshold,
    )
