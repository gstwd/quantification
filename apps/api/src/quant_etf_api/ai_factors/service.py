"""AI 因子服务编排。

编排新闻采集 → AI 分析 → 情绪聚合 → 持久化的完整流程。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from quant_etf_api.ai_factors.analysis.classifier import TagClassifier
from quant_etf_api.ai_factors.analysis.scorer import TrendScorer
from quant_etf_api.ai_factors.analysis.sentiment import SentimentAnalyzer
from quant_etf_api.ai_factors.base import RawNewsItem
from quant_etf_api.ai_factors.data.collector import NewsCollector
from quant_etf_api.infra.ai.client import AIClient
from quant_etf_api.infra.db.repositories.news_item import (
    AISentimentResultRepository,
    DailySentimentAggregateRepository,
    NewsItemRepository,
)

logger = logging.getLogger(__name__)


class AIFactorService:
    """AI 因子服务，编排完整的 AI 分析链路。

    链路：采集 → 清洗 → AI 情绪分析 → 标签分类 → 聚合 → 存储
    """

    def __init__(
        self,
        db: Session,
        client: AIClient,
    ) -> None:
        """初始化 AI 因子服务。

        Args:
            db: SQLAlchemy 数据库会话。
            client: AI 客户端实例。
        """
        self._db = db
        self._client = client
        self._collector = NewsCollector()
        self._analyzer = SentimentAnalyzer(client)
        self._classifier = TagClassifier(client)
        self._scorer = TrendScorer()
        self._news_repo = NewsItemRepository(db)
        self._result_repo = AISentimentResultRepository(db)
        self._agg_repo = DailySentimentAggregateRepository(db)

    # ---- 完整流程 ----

    def run_full_pipeline(
        self,
        target_date: date | None = None,
        platform_ids: list[str] | None = None,
        market_context: str = "",
    ) -> dict[str, int]:
        """执行完整的 AI 分析链路。

        1. 采集新闻（热榜 + RSS）
        2. 新闻去重存储
        3. AI 情绪分析
        4. 标签分类
        5. 情绪聚合
        6. 所有结果持久化

        Args:
            target_date: 目标交易日，默认为今天。
            platform_ids: 指定热榜平台，默认全部。
            market_context: 市场背景描述（如 "央行降准后次日"）。

        Returns:
            统计字典 {"collected": N, "analyzed": N, "aggregated": N}。
        """
        if target_date is None:
            target_date = date.today()

        # 1. 采集
        raw_items = self._collector.fetch_all(platform_ids)
        if not raw_items:
            logger.warning("未采集到任何新闻")
            return {"collected": 0, "analyzed": 0, "aggregated": 0}

        # 2. 存储原始新闻（去重）
        news_rows = self._to_news_rows(raw_items, target_date)
        saved_count = self._news_repo.save_batch(news_rows)
        logger.info("新闻存储: %d 条新增 (共 %d 条采集)", saved_count, len(raw_items))

        # 3. AI 情绪分析
        sentiment_items = self._analyzer.analyze_batch(raw_items, market_context)

        # 4. 标签分类
        sentiment_items = self._classifier.classify_to_asset_tags(sentiment_items)

        # 5. 情绪聚合
        aggregates = self._scorer.aggregate_daily(sentiment_items, target_date)

        # 6. 持久化分析结果
        # 获取已保存新闻的 ID（需要从 DB 重新查询以获取 UUID）
        saved_news = {n.title: n.id for n in self._news_repo.find_by_date(target_date)}
        result_rows = self._to_result_rows(sentiment_items, saved_news, target_date)
        result_count = self._result_repo.save_batch(result_rows)

        # 持久化聚合
        agg_rows = self._to_agg_rows(aggregates)
        agg_count = self._agg_repo.save_batch(agg_rows)

        logger.info(
            "AI 分析链路完成: 采集=%d, 存储=%d, AI分析=%d, 聚合=%d 组",
            len(raw_items),
            saved_count,
            result_count,
            agg_count,
        )

        return {
            "collected": len(raw_items),
            "saved": saved_count,
            "analyzed": result_count,
            "aggregated": agg_count,
        }

    # ---- 内部方法 ----

    def _to_news_rows(
        self,
        items: list[RawNewsItem],
        crawl_date: date,
    ) -> list[dict]:
        """将 RawNewsItem 列表转换为数据库写入行。"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = []
        for item in items:
            rows.append({
                "id": None,  # 让 DB 自动生成 UUID
                "source_id": item.source_id,
                "source_name": item.source_name,
                "title": item.title,
                "url": item.url,
                "rank": item.ranks[0] if item.ranks else None,
                "crawl_date": crawl_date,
                "first_seen_at": _parse_time(item.first_time),
                "last_seen_at": _parse_time(item.last_time),
                "appear_count": item.appear_count,
                "raw_payload": None,
                "created_at": now,
            })
        return rows

    def _to_result_rows(
        self,
        items: list,
        news_map: dict[str, str],
        trade_date: date,
    ) -> list[dict]:
        """将 NewsSentimentItem 列表转换为 AI 分析结果写入行。"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = []
        for item in items:
            news_id = news_map.get(item.title)
            if not news_id:
                continue
            rows.append({
                "id": None,
                "news_id": news_id,
                "trade_date": trade_date,
                "asset_tags": item.asset_tags,
                "topics": item.topics,
                "sentiment_score": item.sentiment_score,
                "attention_score": item.attention_score,
                "relevance_score": item.relevance_score,
                "summary": item.summary,
                "llm_model": self._client.model,
                "llm_response": None,
                "created_at": now,
            })
        return rows

    def _to_agg_rows(self, aggregates: list) -> list[dict]:
        """将 DailySentimentAggregate 列表转换为写入行。"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = []
        for agg in aggregates:
            rows.append({
                "trade_date": agg.date,
                "asset_tag": agg.asset_tag,
                "avg_sentiment": agg.avg_sentiment,
                "weighted_sentiment": agg.weighted_sentiment,
                "total_attention": agg.total_attention,
                "news_count": agg.news_count,
                "top_topics": agg.top_topics,
                "positive_ratio": agg.positive_ratio,
                "negative_ratio": agg.negative_ratio,
                "created_at": now,
            })
        return rows


def _parse_time(time_str: str) -> datetime | None:
    """尝试解析多种时间格式为 datetime。

    Args:
        time_str: 时间字符串。

    Returns:
        datetime 对象或 None。
    """
    if not time_str:
        return None

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    return None
