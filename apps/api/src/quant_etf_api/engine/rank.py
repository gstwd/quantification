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
            assets: 资产得分，key=etf_code。
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
            category_raw = meta.get("category", "broad_index")
            category = _CATEGORY_LABELS.get(category_raw, category_raw)
            rankings.append(
                AssetRanking(
                    etf_code=code,
                    name_cn=meta.get("name_cn", code),
                    category=category,
                    score=score,
                )
            )

        # 排序
        reverse = config.order == "desc"
        rankings.sort(key=lambda r: r.score, reverse=reverse)

        # 截取
        if config.top_n is not None:
            rankings = rankings[: config.top_n]
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
            if context.asset_factors.get((r.etf_code, momentum_factor)) is not None
        ]
        with_momentum.sort(
            key=lambda x: context.asset_factors.get((x[1].etf_code, momentum_factor), 0),
            reverse=True,
        )
        for sub_rank, (idx, r) in enumerate(with_momentum, 1):
            rankings[idx].momentum_rank = sub_rank

        # 估值排名（按估值吸引力降序，吸引力 = 100 - 因子值）
        with_valuation = [
            (i, r)
            for i, r in enumerate(rankings)
            if context.asset_factors.get((r.etf_code, valuation_factor)) is not None
        ]
        with_valuation.sort(
            key=lambda x: 100 - (context.asset_factors.get((x[1].etf_code, valuation_factor)) or 0),
            reverse=True,
        )
        for sub_rank, (idx, r) in enumerate(with_valuation, 1):
            rankings[idx].valuation_rank = sub_rank
