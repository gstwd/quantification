"""每日市场简报的情绪分位与异动分类纯计算。"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SentimentObservation:
    """计算情绪温度所需的单日观测值。"""

    trade_date: date
    breadth_pct: float
    median_change_pct: float
    limit_spread_pct: float
    activity_pct: float
    put_call_ratio: float


@dataclass(frozen=True)
class SentimentResult:
    """情绪温度及其五个历史分位。"""

    score: float
    breadth_percentile: float
    median_percentile: float
    limit_percentile: float
    activity_percentile: float
    pcr_inverse_percentile: float


SENTIMENT_WEIGHTS = {
    "breadth": 0.30,
    "median": 0.25,
    "limit": 0.20,
    "activity": 0.15,
    "pcr": 0.10,
}


def percentile_rank(values: list[float], current: float) -> float:
    """按 PostgreSQL ``percent_rank`` 的近似口径计算当前值历史分位。

    Args:
        values: 历史样本（包含当前值）。
        current: 当前值。

    Returns:
        0 至 100 的分位值。

    Raises:
        ValueError: 历史样本为空时抛出。
    """
    if not values:
        raise ValueError("情绪分位计算至少需要一个历史样本")
    ordered = sorted(values)
    if len(ordered) == 1:
        return 100.0
    rank = bisect_left(ordered, current)
    return round(rank / (len(ordered) - 1) * 100, 1)


def calculate_sentiment(observations: list[SentimentObservation], target_date: date) -> SentimentResult:
    """计算目标日的五因子情绪温度。

    Args:
        observations: 截至目标日的历史观测，最多使用最近 252 个。
        target_date: 目标交易日。

    Returns:
        情绪总分与各分项历史分位。

    Raises:
        ValueError: 目标日不存在或历史为空时抛出。
    """
    sample = observations[-252:]
    target = next((item for item in sample if item.trade_date == target_date), None)
    if target is None:
        raise ValueError(f"目标日 {target_date} 缺少情绪观测")
    breadth = percentile_rank([item.breadth_pct for item in sample], target.breadth_pct)
    median = percentile_rank(
        [item.median_change_pct for item in sample], target.median_change_pct
    )
    limit = percentile_rank(
        [item.limit_spread_pct for item in sample], target.limit_spread_pct
    )
    activity = percentile_rank(
        [item.activity_pct for item in sample], target.activity_pct
    )
    pcr = 100.0 - percentile_rank(
        [item.put_call_ratio for item in sample], target.put_call_ratio
    )
    score = round(
        breadth * SENTIMENT_WEIGHTS["breadth"]
        + median * SENTIMENT_WEIGHTS["median"]
        + limit * SENTIMENT_WEIGHTS["limit"]
        + activity * SENTIMENT_WEIGHTS["activity"]
        + pcr * SENTIMENT_WEIGHTS["pcr"],
        1,
    )
    return SentimentResult(
        score=score,
        breadth_percentile=breadth,
        median_percentile=median,
        limit_percentile=limit,
        activity_percentile=activity,
        pcr_inverse_percentile=pcr,
    )


def sentiment_state(score: float) -> tuple[str, str]:
    """把情绪温度映射为固定五档状态。

    Args:
        score: 0 至 100 的情绪温度。

    Returns:
        状态代码与中文标签。
    """
    if score < 20:
        return "freezing", "冰点"
    if score < 45:
        return "cold", "偏冷"
    if score < 65:
        return "neutral", "中性"
    if score < 85:
        return "hot", "偏热"
    return "boiling", "沸点"


def classify_anomaly(change_1d_pct: float, change_5d_pct: float, relative_pct: float) -> str:
    """按可解释规则给个股或指数异动命名。

    Args:
        change_1d_pct: 单日涨跌幅。
        change_5d_pct: 五日累计涨跌幅。
        relative_pct: 相对行业、主题或宽基的单日超额收益。

    Returns:
        中文异动标签。
    """
    if change_1d_pct <= -5 and change_5d_pct <= -15:
        return "弱势延续"
    if change_1d_pct <= -5 and relative_pct <= -5:
        return "高换手逆势回吐"
    if change_1d_pct >= 9 and change_5d_pct < 0:
        return "反弹未修复"
    if change_5d_pct >= 30:
        return "五日加速"
    if change_1d_pct >= 9 and change_5d_pct >= 10:
        return "趋势强化"
    if change_5d_pct >= 15:
        return "多日趋势"
    if change_1d_pct >= 9:
        return "单日跃升"
    return "相对异动"
