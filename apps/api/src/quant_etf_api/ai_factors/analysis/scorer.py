"""热度/趋势评分器。

从 TrendRadar 项目的 core/analyzer.py 提取核心评分算法，
基于排名、频次、持续时间计算新闻关注度，并按 asset_tag 聚合生成每日情绪汇总。

无需 AI，纯数学计算。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import ClassVar

from quant_etf_api.ai_factors.base import DailySentimentAggregate, NewsSentimentItem

logger = logging.getLogger(__name__)


class TrendScorer:
    """非 AI 的热度/趋势评分器。

    基于排名、频次和时间衰减计算新闻关注度分数 [0, 100]。
    权重配置来自 TrendRadar 的 calculate_news_weight() 默认值。
    """

    # 默认权重配置
    DEFAULT_WEIGHTS: ClassVar[dict[str, float]] = {
        "RANK_WEIGHT": 0.5,       # 排名权重
        "FREQUENCY_WEIGHT": 0.3,  # 频次权重
        "HOTNESS_WEIGHT": 0.2,    # 热度加权重
    }

    # 高排名阈值（排名 <= 此值视为"高位"）
    HIGH_RANK_THRESHOLD: ClassVar[int] = 5

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        high_rank_threshold: int = HIGH_RANK_THRESHOLD,
    ) -> None:
        """初始化热度评分器。

        Args:
            weights: 权重配置，默认使用 DEFAULT_WEIGHTS。
            high_rank_threshold: 高排名阈值。
        """
        self._weights = weights or self.DEFAULT_WEIGHTS.copy()
        self._high_rank_threshold = high_rank_threshold

    def calculate_attention_score(
        self,
        rank: int = 99,
        count: int = 1,
        duration_hours: float | None = None,
    ) -> float:
        """基于排名和频次计算单条新闻的关注度分数。

        算法来源：TrendRadar 的 calculate_news_weight()，简化为单条新闻版本。

        Args:
            rank: 热榜排名（1=榜首），无排名时用 99。
            count: 出现次数。
            duration_hours: 在榜持续时间（小时），用于时间衰减。

        Returns:
            关注度分数 [0, 100]，分数越高表示关注度越高。
        """
        # 排名分：排名越高（数字越小），分数越高
        rank_score = max(0, 11 - min(rank, 10)) * 10  # [10, 100]

        # 频次分：出现次数越多，分数越高（上限 10 次）
        freq_score = min(count, 10) * 10  # [10, 100]

        # 热度加成：基于排名位置
        hotness_score = 0.0
        if rank <= self._high_rank_threshold:
            hotness_score = (self._high_rank_threshold - rank + 1) / self._high_rank_threshold * 100

        # 综合得分
        total = (
            rank_score * self._weights["RANK_WEIGHT"]
            + freq_score * self._weights["FREQUENCY_WEIGHT"]
            + hotness_score * self._weights["HOTNESS_WEIGHT"]
        )

        # 时间衰减（可选）
        if duration_hours is not None and duration_hours > 24:
            decay = max(0.3, 1.0 - (duration_hours - 24) / 168)  # 24h后开始衰减，7天衰减到30%
            total *= decay

        return round(max(0.0, min(100.0, total)), 2)

    def aggregate_daily(
        self,
        items: list[NewsSentimentItem],
        agg_date: date,
        min_relevance: float = 0.2,
    ) -> list[DailySentimentAggregate]:
        """按 asset_tag 聚合每日情绪分析结果。

        对每一条 NewsSentimentItem，按其 asset_tags 分组，
        加权计算每个标签的日均情绪、总关注度等。

        Args:
            items: 情绪分析结果列表。
            agg_date: 聚合日期（交易日）。
            min_relevance: 最低相关度阈值，低于此值的新闻不参与聚合。

        Returns:
            按 asset_tag 分组的 DailySentimentAggregate 列表。
        """
        if not items:
            return []

        # 按 asset_tag 分组
        groups: dict[str, list[NewsSentimentItem]] = defaultdict(list)

        for item in items:
            if item.relevance_score < min_relevance:
                # 低相关度新闻聚合到 "_general" 标签（市场整体情绪）
                groups["_general"].append(item)
                continue

            if not item.asset_tags:
                # 无标签但有相关度 → 聚合到 "_other"
                groups["_other"].append(item)
                continue

            for tag in item.asset_tags:
                groups[tag].append(item)

        # 为每个分组计算聚合指标
        aggregates: list[DailySentimentAggregate] = []

        for tag, tag_items in groups.items():
            if not tag_items:
                continue

            n = len(tag_items)

            # 平均情绪
            sentiments = [it.sentiment_score for it in tag_items]
            avg_sentiment = sum(sentiments) / n

            # 关注度加权情绪（高关注度新闻权重更大）
            attentions = [it.attention_score for it in tag_items]
            total_attn = sum(attentions)
            if total_attn > 0:
                weighted_sentiment = sum(
                    s * a for s, a in zip(sentiments, attentions, strict=True)
                ) / total_attn
            else:
                weighted_sentiment = avg_sentiment

            # 正面/负面占比
            positive_count = sum(1 for s in sentiments if s > 0.15)
            negative_count = sum(1 for s in sentiments if s < -0.15)

            # Top 主题（按出现频率排序）
            topic_counter: dict[str, int] = defaultdict(int)
            for item in tag_items:
                for topic in item.topics:
                    topic_counter[topic] += 1
            top_topics = sorted(topic_counter, key=topic_counter.get, reverse=True)[:5]

            aggregates.append(
                DailySentimentAggregate(
                    date=agg_date,
                    asset_tag=tag,
                    avg_sentiment=round(avg_sentiment, 4),
                    weighted_sentiment=round(weighted_sentiment, 4),
                    total_attention=round(total_attn, 2),
                    news_count=n,
                    top_topics=top_topics,
                    positive_ratio=round(positive_count / n, 4) if n > 0 else 0.0,
                    negative_ratio=round(negative_count / n, 4) if n > 0 else 0.0,
                )
            )

        # 按新闻数量降序排列
        aggregates.sort(key=lambda a: a.news_count, reverse=True)

        logger.info(
            "情绪聚合完成: date=%s, %d 条新闻 → %d 个标签组",
            agg_date,
            len(items),
            len(aggregates),
        )
        return aggregates
