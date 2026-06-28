"""新闻数据仓库。

提供 news_item 和 ai_sentiment_result 表的查询和写入操作。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, func

from quant_etf_api.infra.db.models.core import (
    AISentimentResultModel,
    DailySentimentAggregateModel,
    NewsItemModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository


class NewsItemRepository(BaseRepository):
    """新闻条目仓库。"""

    def save_batch(
        self,
        rows: list[dict[str, Any]],
    ) -> int:
        """批量保存新闻条目（ON CONFLICT DO NOTHING 模式，自动去重）。

        通过前后计数计算实际新增行数，避免依赖 cursor.rowcount
        在 ON CONFLICT 语句中返回 -1 的问题。

        Args:
            rows: 待写入的新闻字典列表。

        Returns:
            实际插入的新记录数。
        """
        if not rows:
            return 0

        from sqlalchemy.dialects.postgresql import insert

        # 分批前后的计数计算实际新增
        before = self._db.query(func.count(NewsItemModel.id)).scalar() or 0

        stmt = insert(NewsItemModel).values(rows).on_conflict_do_nothing(
            index_elements=["source_id", "title", "crawl_date"],
        )
        try:
            self._db.execute(stmt)
            self._db.commit()

            after = self._db.query(func.count(NewsItemModel.id)).scalar() or 0
            return max(0, after - before)
        except Exception:
            self._db.rollback()
            raise

    def find_by_date(
        self,
        crawl_date: date,
        source_ids: list[str] | None = None,
    ) -> list[NewsItemModel]:
        """查询指定日期的新闻。

        Args:
            crawl_date: 采集日期。
            source_ids: 限定来源平台，None=全部。

        Returns:
            NewsItemModel 列表。
        """
        q = self._db.query(NewsItemModel).filter(NewsItemModel.crawl_date == crawl_date)
        if source_ids:
            q = q.filter(NewsItemModel.source_id.in_(source_ids))
        return q.all()

    def find_latest_crawl_date(self) -> date | None:
        """查询最近的采集日期。

        Returns:
            最近采集日期，无数据时返回 None。
        """
        row = (
            self._db.query(func.max(NewsItemModel.crawl_date))
            .scalar()
        )
        return row


class AISentimentResultRepository(BaseRepository):
    """AI 情绪分析结果仓库。"""

    def save_batch(
        self,
        rows: list[dict[str, Any]],
    ) -> int:
        """批量保存 AI 分析结果（ON CONFLICT DO UPDATE 模式）。

        所有输入行都会生效（插入或更新），直接返回输入行数。

        Args:
            rows: 待写入的字典列表。

        Returns:
            写入的记录数（= 输入行数）。
        """
        if not rows:
            return 0

        from sqlalchemy.dialects.postgresql import insert

        stmt = insert(AISentimentResultModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["news_id"],
            set_={
                "trade_date": stmt.excluded.trade_date,
                "asset_tags": stmt.excluded.asset_tags,
                "topics": stmt.excluded.topics,
                "sentiment_score": stmt.excluded.sentiment_score,
                "attention_score": stmt.excluded.attention_score,
                "relevance_score": stmt.excluded.relevance_score,
                "summary": stmt.excluded.summary,
                "llm_model": stmt.excluded.llm_model,
                "llm_response": stmt.excluded.llm_response,
            },
        )
        try:
            self._db.execute(stmt)
            self._db.commit()
            return len(rows)
        except Exception:
            self._db.rollback()
            raise

    def find_existing_news_ids(self, news_ids: list[str]) -> set[str]:
        """查询已有 AI 分析记录的 news_id 集合。

        用于跳过已分析新闻，避免重复调用 LLM。

        Args:
            news_ids: 待检查的 news_id 列表。

        Returns:
            已有分析记录的 news_id 集合。
        """
        if not news_ids:
            return set()

        rows = (
            self._db.query(AISentimentResultModel.news_id)
            .filter(AISentimentResultModel.news_id.in_(news_ids))
            .all()
        )
        return {r[0] for r in rows}

    def find_by_news_ids(
        self,
        news_ids: list[str],
    ) -> list[AISentimentResultModel]:
        """按 news_id 批量查询已有分析结果。

        用于复用已分析新闻的结果，参与后续聚合。

        Args:
            news_ids: news_id 列表。

        Returns:
            AISentimentResultModel 列表。
        """
        if not news_ids:
            return []

        return (
            self._db.query(AISentimentResultModel)
            .filter(AISentimentResultModel.news_id.in_(news_ids))
            .all()
        )


class DailySentimentAggregateRepository(BaseRepository):
    """每日情绪聚合仓库。"""

    def save_batch(
        self,
        rows: list[dict[str, Any]],
    ) -> int:
        """批量保存情绪聚合数据（ON CONFLICT DO UPDATE 模式）。

        所有输入行都会生效（插入或更新），直接返回输入行数。

        Args:
            rows: 待写入的字典列表。

        Returns:
            写入的记录数（= 输入行数）。
        """
        if not rows:
            return 0

        from sqlalchemy.dialects.postgresql import insert

        stmt = insert(DailySentimentAggregateModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["trade_date", "asset_tag"],
            set_={
                "avg_sentiment": stmt.excluded.avg_sentiment,
                "weighted_sentiment": stmt.excluded.weighted_sentiment,
                "total_attention": stmt.excluded.total_attention,
                "news_count": stmt.excluded.news_count,
                "top_topics": stmt.excluded.top_topics,
                "positive_ratio": stmt.excluded.positive_ratio,
                "negative_ratio": stmt.excluded.negative_ratio,
            },
        )
        try:
            self._db.execute(stmt)
            self._db.commit()
            return len(rows)
        except Exception:
            self._db.rollback()
            raise

    def find_by_date_range(
        self,
        start_date: date,
        end_date: date,
        asset_tag: str | None = None,
    ) -> list[DailySentimentAggregateModel]:
        """查询日期范围内的情绪聚合数据。

        Args:
            start_date: 起始日期（含）。
            end_date: 截止日期（含）。
            asset_tag: 限定资产标签，None=全部。

        Returns:
            DailySentimentAggregateModel 列表。
        """
        q = (
            self._db.query(DailySentimentAggregateModel)
            .filter(
                and_(
                    DailySentimentAggregateModel.trade_date >= start_date,
                    DailySentimentAggregateModel.trade_date <= end_date,
                )
            )
        )
        if asset_tag:
            q = q.filter(DailySentimentAggregateModel.asset_tag == asset_tag)
        return q.order_by(DailySentimentAggregateModel.trade_date).all()
