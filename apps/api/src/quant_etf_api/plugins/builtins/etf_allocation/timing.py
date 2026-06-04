"""市场择时评估模块。

综合估值、趋势、量能三个维度判断当前市场环境，输出择时信号。
估值使用 ETF 跟踪指数的 PE/PB 百分位，趋势使用 MA60 位置，量能使用 20 日量比。
"""

from __future__ import annotations

import logging
from typing import Any

from quant_etf_api.domain.strategies.models import TimingSignal

logger = logging.getLogger(__name__)


def _valuation_score(pe_pct: float | None, pb_pct: float | None) -> float | None:
    """将 PE/PB 百分位映射为估值得分（0-100）。

    百分位越低表示越低估，得分越高（越值得买入）。
    PE 和 PB 各占 50% 权重，只有一个时退化为单因子。

    Args:
        pe_pct: PE 历史百分位（0-100），None 表示无数据。
        pb_pct: PB 历史百分位（0-100），None 表示无数据。

    Returns:
        估值得分（0-100），两者都无数据时返回 None。
    """
    scores: list[float] = []
    if pe_pct is not None:
        # 百分位越低 → 越便宜 → 得分越高
        scores.append(100.0 - pe_pct)
    if pb_pct is not None:
        scores.append(100.0 - pb_pct)
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)


def _trend_score(close_price: float | None, ma60: float | None) -> float | None:
    """将价格相对 MA60 的位置映射为趋势得分（0-100）。

    价格在 MA60 之上得分高（趋势向好），之下得分低。

    Args:
        close_price: 当日收盘价。
        ma60: 60 日均线价格。

    Returns:
        趋势得分（0-100），数据不足时返回 None。
    """
    if close_price is None or ma60 is None or ma60 <= 0:
        return None
    # 偏离度：(价格 - MA60) / MA60 × 100
    deviation_pct = (close_price - ma60) / ma60 * 100
    # 分段线性映射：偏离 -10% ~ +10% → 得分 0 ~ 100
    if deviation_pct <= -10:
        return 0.0
    if deviation_pct >= 10:
        return 100.0
    return round(50 + deviation_pct * 5, 1)


def _volume_score(volume_ratio: float) -> float:
    """将 20 日量比映射为量能得分（0-100）。

    温和放量（1.0-2.0）得分高，极端缩量或放量得分低。

    Args:
        volume_ratio: 20 日量比。

    Returns:
        量能得分（0-100）。
    """
    if volume_ratio < 0.3:
        return 10.0
    if volume_ratio < 0.5:
        return 20.0
    if volume_ratio < 0.8:
        return 35.0
    if volume_ratio < 1.0:
        return 50.0
    if volume_ratio < 1.3:
        return 70.0
    if volume_ratio < 1.5:
        return 80.0
    if volume_ratio < 2.0:
        return 85.0
    if volume_ratio < 3.0:
        return 70.0
    # 极端放量（>3.0），可能是顶部信号
    return 50.0


def assess_timing(
    pe_pct: float | None,
    pb_pct: float | None,
    close_price: float | None,
    ma60: float | None,
    volume_ratio: float,
    index_5d_return: float | None = None,
) -> TimingSignal:
    """综合评估市场择时信号。

    三个维度加权合成：
    - 估值（40%）：PE/PB 百分位越低越值得买入
    - 趋势（40%）：价格在 MA60 之上为多头
    - 量能（20%）：温和放量为佳

    Args:
        pe_pct: 跟踪指数 PE 百分位（0-100）。
        pb_pct: 跟踪指数 PB 百分位（0-100）。
        close_price: ETF 当日收盘价。
        ma60: ETF 60 日均线。
        volume_ratio: 20 日量比。
        index_5d_return: 指数近 5 日收益率（%），辅助判断。

    Returns:
        TimingSignal，包含 regime、confidence、label、factors。
    """
    val_score = _valuation_score(pe_pct, pb_pct)
    trend_s = _trend_score(close_price, ma60)
    vol_score = _volume_score(volume_ratio)

    # 收集有效得分，按权重加权
    weights_and_scores: list[tuple[float, float]] = []
    if val_score is not None:
        weights_and_scores.append((0.4, val_score))
    if trend_s is not None:
        weights_and_scores.append((0.4, trend_s))
    # 量能始终有值（默认 1.0），但权重较低
    weights_and_scores.append((0.2, vol_score))

    # 关键数据缺失检查：估值和趋势都无数据时，仅凭量能不能给出有效信号
    has_valuation = val_score is not None
    has_trend = trend_s is not None
    if not has_valuation and not has_trend:
        return TimingSignal(
            regime="neutral",
            confidence=0.0,
            label="数据不足",
            factors={
                "reason": "估值和趋势数据均缺失，无法生成有效择时信号",
                "valuation_score": None,
                "trend_score": None,
                "volume_score": vol_score,
            },
        )

    # 加权合成
    total_weight = sum(w for w, _ in weights_and_scores)
    composite = sum(w * s for w, s in weights_and_scores) / total_weight

    # 根据综合得分判定 regime
    # 得分越高表示越值得买入（低估+多头+温和放量）
    if composite >= 65:
        regime = "offensive"
        label = "进攻"
    elif composite <= 35:
        regime = "defensive"
        label = "防守"
    else:
        regime = "neutral"
        label = "观望"

    # 确信度：离阈值越远越确定
    if regime == "offensive":
        confidence = min(100.0, (composite - 65) * 2 + 60)
    elif regime == "defensive":
        confidence = min(100.0, (35 - composite) * 2 + 60)
    else:
        # 中间区域，确信度较低
        confidence = max(20.0, 60 - abs(composite - 50))

    factors: dict[str, Any] = {
        "composite_score": round(composite, 1),
        "valuation_score": val_score,
        "trend_score": trend_s,
        "volume_score": vol_score,
        "pe_percentile": pe_pct,
        "pb_percentile": pb_pct,
        "volume_ratio": volume_ratio,
    }
    if index_5d_return is not None:
        factors["index_5d_return"] = index_5d_return

    return TimingSignal(
        regime=regime,
        confidence=round(confidence, 1),
        label=label,
        factors=factors,
    )
