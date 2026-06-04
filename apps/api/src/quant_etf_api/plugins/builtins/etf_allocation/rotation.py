"""资产轮动排名模块。

按动量 + 估值对 ETF 排名，输出综合得分排序的列表。
动量使用 20 日收益率，估值使用 PE/PB 百分位。
板块分类基于 EtfUniverseModel.category 字段。
"""

from __future__ import annotations

import logging
from typing import Any

from quant_etf_api.domain.strategies.models import AssetRanking

logger = logging.getLogger(__name__)

# 板块分类中文映射
_CATEGORY_LABELS: dict[str, str] = {
    "broad_index": "宽基",
    "sector": "行业",
    "theme": "主题",
    "bond": "债券",
    "commodity": "商品",
    "cross_border": "跨境",
    "strategy": "策略",
}


def _momentum_score(return_20d: float | None, return_5d: float | None) -> float:
    """将收益率映射为动量得分（0-100）。

    20 日收益率为主（70%），5 日收益率为辅（30%）。
    收益率越高，动量得分越高。

    Args:
        return_20d: 20 日收益率（%）。
        return_5d: 5 日收益率（%）。

    Returns:
        动量得分（0-100）。
    """
    # 20 日收益率分段映射
    if return_20d is not None:
        if return_20d > 15:
            s20 = 95.0
        elif return_20d > 10:
            s20 = 85.0
        elif return_20d > 5:
            s20 = 70.0
        elif return_20d > 2:
            s20 = 60.0
        elif return_20d > 0:
            s20 = 50.0
        elif return_20d > -2:
            s20 = 40.0
        elif return_20d > -5:
            s20 = 30.0
        elif return_20d > -10:
            s20 = 20.0
        else:
            s20 = 10.0
    else:
        s20 = 50.0  # 无数据时中性

    # 5 日收益率分段映射
    if return_5d is not None:
        if return_5d > 5:
            s5 = 90.0
        elif return_5d > 3:
            s5 = 75.0
        elif return_5d > 1:
            s5 = 60.0
        elif return_5d > 0:
            s5 = 50.0
        elif return_5d > -1:
            s5 = 40.0
        elif return_5d > -3:
            s5 = 30.0
        elif return_5d > -5:
            s5 = 20.0
        else:
            s5 = 10.0
    else:
        s5 = 50.0  # 无数据时中性

    return round(s20 * 0.7 + s5 * 0.3, 1)


def _valuation_attractiveness(pe_pct: float | None, pb_pct: float | None) -> float | None:
    """将 PE/PB 百分位映射为估值吸引力得分（0-100）。

    百分位越低表示越便宜，吸引力得分越高。

    Args:
        pe_pct: PE 百分位（0-100）。
        pb_pct: PB 百分位（0-100）。

    Returns:
        估值吸引力得分（0-100），无数据时返回 None。
    """
    scores: list[float] = []
    if pe_pct is not None:
        scores.append(100.0 - pe_pct)
    if pb_pct is not None:
        scores.append(100.0 - pb_pct)
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)


def rank_etf_assets(
    universe: list[dict[str, Any]],
    etf_bars: dict[str, dict[str, Any]],
    index_valuation: dict[str, dict[str, Any]],
    etf_index_map: dict[str, str],
) -> list[AssetRanking]:
    """对 ETF 宇宙按动量 + 估值综合排名。

    综合得分 = 动量得分 × 60% + 估值吸引力 × 40%（无估值数据时退化为纯动量）。

    Args:
        universe: ETF 宇宙列表，每项含 etf_code、name_cn、category 等。
        etf_bars: ETF 行情数据，key=etf_code，value 含 return_20d、return_5d 等。
        index_valuation: 指数估值数据，key=index_code，value 含 pe_percentile、pb_percentile。
        etf_index_map: ETF 代码到跟踪指数代码的映射。

    Returns:
        按综合得分降序排列的 AssetRanking 列表。
    """
    rankings: list[AssetRanking] = []

    for item in universe:
        code = item["etf_code"]
        name_cn = item.get("name_cn", code)
        category_raw = item.get("category", "broad_index")
        category = _CATEGORY_LABELS.get(category_raw, category_raw)

        bars = etf_bars.get(code, {})
        return_20d = bars.get("return_20d")
        return_5d = bars.get("return_5d")
        volatility = bars.get("volatility_20d")

        # 动量得分
        mom_score = _momentum_score(return_20d, return_5d)

        # 估值吸引力
        index_code = etf_index_map.get(code)
        val_score = None
        if index_code:
            val_data = index_valuation.get(index_code, {})
            pe_pct = val_data.get("pe_percentile")
            pb_pct = val_data.get("pb_percentile")
            val_score = _valuation_attractiveness(pe_pct, pb_pct)

        # 综合得分
        if val_score is not None:
            composite = round(mom_score * 0.6 + val_score * 0.4, 1)
        else:
            composite = mom_score

        details: dict[str, Any] = {
            "momentum_score": mom_score,
            "valuation_score": val_score,
            "return_20d": return_20d,
            "return_5d": return_5d,
            "volatility_20d": volatility,
        }
        if index_code:
            details["index_code"] = index_code

        rankings.append(
            AssetRanking(
                etf_code=code,
                name_cn=name_cn,
                category=category,
                score=composite,
                details=details,
            )
        )

    # 按综合得分降序排列，分配排名
    rankings.sort(key=lambda r: r.score, reverse=True)

    # 动量排名（按 return_20d 降序）
    with_momentum = [(i, r) for i, r in enumerate(rankings) if r.details.get("return_20d") is not None]
    with_momentum.sort(key=lambda x: x[1].details["return_20d"], reverse=True)
    for rank, (idx, r) in enumerate(with_momentum, 1):
        rankings[idx].momentum_rank = rank

    # 估值排名（按估值吸引力降序）
    with_valuation = [(i, r) for i, r in enumerate(rankings) if r.details.get("valuation_score") is not None]
    with_valuation.sort(key=lambda x: x[1].details["valuation_score"], reverse=True)
    for rank, (idx, r) in enumerate(with_valuation, 1):
        rankings[idx].valuation_rank = rank

    return rankings
