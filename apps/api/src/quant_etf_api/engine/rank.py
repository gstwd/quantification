"""排名模块：按综合得分排序并截取 TopN/BottomN。

输出 AssetRanking 列表，包含综合排名和子维度排名。
"""

from __future__ import annotations

import logging
from typing import Protocol

from quant_etf_api.domain.strategies.models import AssetRanking
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import RankConfig

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


class RankEngine(Protocol):
    """排名引擎协议。"""

    def rank(
        self,
        config: RankConfig,
        assets: dict[str, float],
        context: EngineContext,
    ) -> list[AssetRanking]:
        """排序并截取 TopN/BottomN。

        Args:
            config: 排名配置。
            assets: 资产得分，key=index_code。
            context: 引擎上下文。

        Returns:
            排序后的 AssetRanking 列表。
        """
        ...


class DefaultRankEngine:
    """默认排名引擎。"""

    def rank(
        self,
        config: RankConfig,
        assets: dict[str, float],
        context: EngineContext,
    ) -> list[AssetRanking]:
        """排序并截取 TopN/BottomN。"""
        if not assets:
            return []

        # 构建排名列表
        rankings: list[AssetRanking] = []
        for code, score in assets.items():
            meta = context.asset_metadata.get(code, {})
            # benchmark_index 没有分类字段：未提供分类时留空，不再伪造"宽基"标签
            category_raw = meta.get("category") or ""
            category = _CATEGORY_LABELS.get(category_raw, category_raw)
            rankings.append(
                AssetRanking(
                    index_code=code,
                    name_cn=meta.get("name_cn", code),
                    category=category,
                    score=score,
                )
            )

        # 排序：得分相同时按指数代码升序决定先后，结果不依赖 universe 的输入顺序
        if config.order == "desc":
            rankings.sort(key=lambda r: (-r.score, r.index_code))
        else:
            rankings.sort(key=lambda r: (r.score, r.index_code))

        # 截取
        if config.top_n is not None:
            rankings = _truncate_with_buffer(
                rankings, config.top_n, config.buffer_n, context.current_holdings
            )
        elif config.bottom_n is not None:
            rankings = rankings[-config.bottom_n :]

        # 分配子维度排名（动量和估值）
        self._assign_sub_rankings(config, rankings, context)

        return rankings

    def _assign_sub_rankings(
        self, config: RankConfig, rankings: list[AssetRanking], context: EngineContext
    ) -> None:
        """为排名项分配动量和估值子排名。"""
        momentum_factor = config.momentum_factor
        valuation_factor = config.valuation_factor

        # 动量排名（按动量因子降序）
        with_momentum = [
            (i, r)
            for i, r in enumerate(rankings)
            if context.asset_factors.get((r.index_code, momentum_factor)) is not None
        ]
        with_momentum.sort(
            key=lambda x: context.asset_factors.get((x[1].index_code, momentum_factor), 0),
            reverse=True,
        )
        for sub_rank, (idx, r) in enumerate(with_momentum, 1):
            rankings[idx].momentum_rank = sub_rank

        # 估值排名（按因子原值升序：数值越低越便宜，排名越前）。不再假设因子是
        # 0-100 百分位并计算"100 - 值"；对百分位因子两者排序完全一致
        with_valuation = [
            (i, r)
            for i, r in enumerate(rankings)
            if context.asset_factors.get((r.index_code, valuation_factor)) is not None
        ]
        with_valuation.sort(
            key=lambda x: context.asset_factors.get((x[1].index_code, valuation_factor)) or 0,
        )
        for sub_rank, (idx, r) in enumerate(with_valuation, 1):
            rankings[idx].valuation_rank = sub_rank


def _truncate_with_buffer(
    rankings: list[AssetRanking],
    top_n: int,
    buffer_n: int | None,
    holdings: dict[str, float] | None,
) -> list[AssetRanking]:
    """截取前 N 名；启用缓冲带时优先保留排名仍在前 N+buffer 内的老持仓。

    老持仓按排名先后保留（最多 N 个），剩余名额按排名补足新资产，结果仍按排名排序。
    没有持仓信息或未启用缓冲带时与严格截取前 N 名一致。

    Args:
        rankings: 已排序的全部排名项。
        top_n: 目标持有数。
        buffer_n: 缓冲名次，None 表示不启用。
        holdings: 调仓前实际持仓。

    Returns:
        截取后的排名项。
    """
    if not buffer_n or not holdings:
        return rankings[:top_n]
    held = {code for code, weight in holdings.items() if weight > 0}
    keep = [r for r in rankings[: top_n + buffer_n] if r.index_code in held][:top_n]
    kept = {r.index_code for r in keep}
    fill = [r for r in rankings if r.index_code not in kept][: top_n - len(keep)]
    chosen = kept | {r.index_code for r in fill}
    return [r for r in rankings if r.index_code in chosen]
