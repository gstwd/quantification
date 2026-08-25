"""新闻数据仓库。

提供 news_item 和 ai_sentiment_result 表的查询和写入操作。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.dialects.postgresql import JSONB

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

        stmt = (
            insert(NewsItemModel)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["source_id", "title", "crawl_date"],
            )
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
        row = self._db.query(func.max(NewsItemModel.crawl_date)).scalar()
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
        """查询已有有效 AI 分析记录的 news_id 集合。

        仅返回"有意义"的分析结果（sentiment_score 非零 或 relevance_score > 0
        或 asset_tags 非空），跳过 AI 生成的全零默认值，确保空结果能被重新分析。

        Args:
            news_ids: 待检查的 news_id 列表。

        Returns:
            已有有效分析记录的 news_id 集合。
        """
        if not news_ids:
            return set()

        from sqlalchemy import or_

        rows = (
            self._db.query(AISentimentResultModel.news_id)
            .filter(
                AISentimentResultModel.news_id.in_(news_ids),
                or_(
                    AISentimentResultModel.sentiment_score != 0,
                    AISentimentResultModel.relevance_score > 0,
                ),
            )
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

    def find_news_by_tag(
        self,
        trade_date: date,
        asset_tag: str,
    ) -> list[dict[str, object]]:
        """查询指定交易日、指定资产标签下的所有新闻明细。

        通过 JOIN news_item 和 ai_sentiment_result 表，
        筛选 asset_tags JSON 数组中包含指定标签的记录。

        Args:
            trade_date: 交易日。
            asset_tag: 资产标签（如 "科技"、"军工"）。

        Returns:
            新闻明细列表，每项含 title/url/sentiment_score/attention_score/source_name。
        """
        rows = (
            self._db.query(
                NewsItemModel.title,
                NewsItemModel.url,
                NewsItemModel.source_name,
                AISentimentResultModel.sentiment_score,
                AISentimentResultModel.attention_score,
            )
            .join(
                AISentimentResultModel,
                AISentimentResultModel.news_id == NewsItemModel.id,
            )
            .filter(
                AISentimentResultModel.trade_date == trade_date,
                AISentimentResultModel.asset_tags.cast(JSONB).contains([asset_tag]),
            )
            .order_by(AISentimentResultModel.attention_score.desc())
            .all()
        )
        return [
            {
                "title": row.title or "",
                "url": row.url or "",
                "source_name": row.source_name or "",
                "sentiment_score": float(row.sentiment_score or 0),
                "attention_score": float(row.attention_score or 0),
            }
            for row in rows
        ]


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
        q = self._db.query(DailySentimentAggregateModel).filter(
            and_(
                DailySentimentAggregateModel.trade_date >= start_date,
                DailySentimentAggregateModel.trade_date <= end_date,
            )
        )
        if asset_tag:
            q = q.filter(DailySentimentAggregateModel.asset_tag == asset_tag)
        return q.order_by(DailySentimentAggregateModel.trade_date).all()


class MarketSynthesisRepository(BaseRepository):
    """每日市场综合研判仓库。"""

    def save(self, row: dict[str, Any]) -> bool:
        """保存或更新市场研判（upsert on trade_date）。

        Args:
            row: 待写入的字典，含 id/trade_date/content 等字段。

        Returns:
            True 表示写入成功。
        """
        from sqlalchemy.dialects.postgresql import insert

        from quant_etf_api.infra.db.models.core import MarketSynthesisModel

        stmt = insert(MarketSynthesisModel).values(row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["trade_date"],
            set_={
                "content": stmt.excluded.content,
                "sentiment_summary": stmt.excluded.sentiment_summary,
                "key_topics": stmt.excluded.key_topics,
                "risk_notes": stmt.excluded.risk_notes,
                "llm_model": stmt.excluded.llm_model,
            },
        )
        try:
            self._db.execute(stmt)
            self._db.commit()
            return True
        except Exception:
            self._db.rollback()
            logger = __import__("logging").getLogger(__name__)
            logger.error("市场研判保存失败", exc_info=True)
            return False

    def find_by_date(self, target_date: date) -> Any | None:
        """查询指定日期的市场研判。

        Args:
            target_date: 交易日。

        Returns:
            MarketSynthesisModel 实例或 None。
        """
        from quant_etf_api.infra.db.models.core import MarketSynthesisModel

        return (
            self._db.query(MarketSynthesisModel)
            .filter(MarketSynthesisModel.trade_date == target_date)
            .first()
        )

    def find_by_date_range(
        self, start_date: date, end_date: date
    ) -> list[Any]:
        """查询日期范围内的市场研判。

        Args:
            start_date: 起始日期（含）。
            end_date: 截止日期（含）。

        Returns:
            MarketSynthesisModel 列表，按日期升序。
        """
        from quant_etf_api.infra.db.models.core import MarketSynthesisModel

        return (
            self._db.query(MarketSynthesisModel)
            .filter(
                MarketSynthesisModel.trade_date >= start_date,
                MarketSynthesisModel.trade_date <= end_date,
            )
            .order_by(MarketSynthesisModel.trade_date)
            .all()
        )
