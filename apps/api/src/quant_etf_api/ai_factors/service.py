"""AI 因子服务编排。

编排新闻采集 → AI 分析 → 情绪聚合 → 市场研判 → 持久化的完整流程。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from quant_etf_api.ai_factors.analysis.classifier import TagClassifier
from quant_etf_api.ai_factors.analysis.scorer import TrendScorer
from quant_etf_api.ai_factors.analysis.sentiment import SentimentAnalyzer
from quant_etf_api.ai_factors.analysis.synthesis import MarketSynthesisAnalyzer
from quant_etf_api.ai_factors.base import (
    RawNewsItem,
    build_available_tags,
)
from quant_etf_api.ai_factors.data.collector import NewsCollector
from quant_etf_api.infra.ai.client import AIClient
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.news_item import (
    AISentimentResultRepository,
    DailySentimentAggregateRepository,
    MarketSynthesisRepository,
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
        max_analysis_items: int | None = None,
    ) -> None:
        """初始化 AI 因子服务。

        Args:
            db: SQLAlchemy 数据库会话。
            client: AI 客户端实例。
            max_analysis_items: 单次 LLM 分析最大新闻数，None 时从配置读取。
        """
        from quant_etf_api.config.settings import get_settings

        if max_analysis_items is None:
            try:
                max_analysis_items = get_settings().ai_max_analysis_items
            except Exception:
                max_analysis_items = 150

        self._db = db
        self._client = client
        self._collector = NewsCollector()
        self._analyzer = SentimentAnalyzer(
            client,
            max_analysis_items=max_analysis_items,
        )
        self._classifier = TagClassifier(client)
        self._scorer = TrendScorer()
        self._synthesizer = MarketSynthesisAnalyzer(client)
        self._news_repo = NewsItemRepository(db)
        self._result_repo = AISentimentResultRepository(db)
        self._agg_repo = DailySentimentAggregateRepository(db)
        self._synthesis_repo = MarketSynthesisRepository(db)
        self._benchmark_repo = BenchmarkIndexRepository(db)

        # 从 DB 加载动态关键词→标签映射（失败时回退到静态默认值）
        try:
            self._classifier.load_keyword_map(db)
        except Exception:
            logger.warning("加载关键词标签配置失败，将使用静态默认值", exc_info=True)

        # === 多源新闻搜索增强（可选，仅在配置了搜索提供者时激活） ===
        self._enhancer: NewsEnhancer | None = None
        try:
            from quant_etf_api.config.settings import get_settings
            settings = get_settings()
            if any([
                getattr(settings, "tavily_api_keys", None),
                getattr(settings, "bocha_api_keys", None),
                getattr(settings, "brave_api_keys", None),
                getattr(settings, "serpapi_api_keys", None),
                getattr(settings, "anspire_api_keys", None),
                getattr(settings, "searxng_urls", None),
            ]):
                from quant_etf_api.infra.news_sources.manager import NewsSourceManager
                from quant_etf_api.services.news_enhancer import NewsEnhancer
                manager = NewsSourceManager(
                    tavily_keys=getattr(settings, "tavily_api_keys", None),
                    bocha_keys=getattr(settings, "bocha_api_keys", None),
                    brave_keys=getattr(settings, "brave_api_keys", None),
                    serpapi_keys=getattr(settings, "serpapi_api_keys", None),
                    anspire_keys=getattr(settings, "anspire_api_keys", None),
                    searxng_urls=getattr(settings, "searxng_urls", None),
                )
                self._enhancer = NewsEnhancer(manager)
                logger.info("多源新闻搜索增强已激活")
        except Exception:
            logger.warning("多源新闻搜索增强初始化失败，将仅使用热榜数据", exc_info=True)

    # ---- 完整流程 ----

    def run_full_pipeline(
        self,
        target_date: date | None = None,
        platform_ids: list[str] | None = None,
    ) -> dict[str, int]:
        """执行完整的 AI 分析链路。

        1. 采集新闻（热榜 + RSS）
        2. 新闻去重存储
        3. 过滤已分析新闻，仅对新新闻调用 LLM
        4. AI 情绪分析（仅未分析新闻）
        5. 标签分类（全部新闻）
        6. 情绪聚合
        7. 所有结果持久化

        Args:
            target_date: 目标交易日，默认为今天。
            platform_ids: 指定热榜平台，默认全部。

        Returns:
            统计字典 {"collected": N, "saved": N, "analyzed": N, "aggregated": N}。
        """
        if target_date is None:
            target_date = date.today()

        # 1. 采集
        raw_items = self._collector.fetch_all(platform_ids)
        if not raw_items:
            logger.warning("未采集到任何新闻")
            return {"collected": 0, "saved": 0, "analyzed": 0, "aggregated": 0}

        # 1.5 多源新闻搜索增强（可选，失败不影响主链路）
        if self._enhancer is not None:
            try:
                enhanced = self._enhancer.enhance(raw_items)
                if enhanced:
                    raw_items = raw_items + enhanced
                    logger.info("新闻增强: +%d 条", len(enhanced))
            except Exception:
                logger.warning("新闻增强失败，使用原始热榜数据", exc_info=True)

        # 2. 存储原始新闻（去重）
        news_rows = self._to_news_rows(raw_items, target_date)
        saved_count = self._news_repo.save_batch(news_rows)
        logger.info("新闻存储: %d 条新增 (共 %d 条采集)", saved_count, len(raw_items))

        # 2.5 获取已保存新闻的 (source_id, title) → id 映射
        saved_news = {
            (n.source_id, n.title): n.id for n in self._news_repo.find_by_date(target_date)
        }

        # raw_items 的 (source_id, title) → RawNewsItem 映射（复合键避免跨平台同名标题冲突）
        item_key_to_raw = {(it.source_id, it.title): it for it in raw_items}

        # 2.6 检查已分析新闻，拆分未分析/已分析
        all_news_ids = list(saved_news.values())
        existing_ids = self._result_repo.find_existing_news_ids(all_news_ids)
        skipped_count = len(existing_ids)

        # 拆分 raw_items：按 (source_id, title) 对应 news_id 是否已分析
        unanalyzed_raw: list[RawNewsItem] = []
        for item in raw_items:
            key = (item.source_id, item.title)
            nid = saved_news.get(key)
            if nid is None or nid not in existing_ids:
                unanalyzed_raw.append(item)

        if skipped_count > 0:
            logger.info(
                "跳过 %d 条已分析新闻，待分析 %d 条",
                skipped_count,
                len(unanalyzed_raw),
            )

        # 3. AI 情绪分析（注入动态指数标签，仅分析未分析新闻）
        index_map = self._get_dynamic_index_map()
        available_tags = build_available_tags(index_map)
        new_sentiment_items: list = []
        if unanalyzed_raw:
            new_sentiment_items = self._analyzer.analyze_batch(
                unanalyzed_raw,
                available_tags,
            )
        else:
            new_sentiment_items = []

        # 3.5 从 DB 加载已分析新闻的 NewsSentimentItem（复用已有分析结果）
        existing_sentiment_items: list = []
        if existing_ids:
            existing_results = self._result_repo.find_by_news_ids(list(existing_ids))
            # 构建 news_id → (source_id, title) 反向映射
            id_to_key = {v: k for k, v in saved_news.items()}
            existing_sentiment_items = self._db_results_to_sentiment_items(
                existing_results,
                id_to_key,
                item_key_to_raw,
            )

        # 合并全部 sentiment_items
        sentiment_items = new_sentiment_items + existing_sentiment_items

        # 4. 标签分类（全部新闻统一分类）
        sentiment_items = self._classifier.classify_to_asset_tags(sentiment_items, available_tags)

        # 5. 情绪聚合
        aggregates = self._scorer.aggregate_daily(sentiment_items, target_date)

        # 6. 持久化分析结果
        result_rows = self._to_result_rows(sentiment_items, saved_news, target_date)
        result_count = self._result_repo.save_batch(result_rows)

        # 持久化聚合
        agg_rows = self._to_agg_rows(aggregates)
        agg_count = self._agg_repo.save_batch(agg_rows)

        # 7. 市场综合研判（基于聚合结果，失败不影响主链路）
        synthesis_ok = False
        try:
            synthesis_ok = self._run_market_synthesis(aggregates, target_date)
        except Exception:
            logger.exception("市场研判生成失败，不影响主链路")

        logger.info(
            "AI 分析链路完成: 采集=%d, 存储=%d, 跳过=%d, 新分析=%d, AI分析=%d, 聚合=%d 组, 研判=%s",
            len(raw_items),
            saved_count,
            skipped_count,
            len(new_sentiment_items),
            result_count,
            agg_count,
            "OK" if synthesis_ok else "跳过",
        )

        return {
            "collected": len(raw_items),
            "saved": saved_count,
            "analyzed": result_count,
            "aggregated": agg_count,
            "synthesis": 1 if synthesis_ok else 0,
        }

    # ---- 内部方法 ----

    def _get_dynamic_index_map(self) -> dict[str, str]:
        """从 benchmark_index 表动态加载活跃指数映射。

        实时查询数据库，确保每次分析都使用最新的指数列表。
        查询失败时回退到静态的 INDEX_TAGS。

        Returns:
            index_code → name_cn 的映射字典。
        """
        try:
            rows = self._benchmark_repo.find_active()
            if rows:
                return {r.index_code: r.name_cn for r in rows}
        except Exception:
            logger.warning("动态加载指数标签失败，回退到静态列表", exc_info=True)

        from quant_etf_api.ai_factors.base import INDEX_TAGS

        return dict(INDEX_TAGS)

    def _to_news_rows(
        self,
        items: list[RawNewsItem],
        crawl_date: date,
    ) -> list[dict]:
        """将 RawNewsItem 列表转换为数据库写入行。"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = []
        for item in items:
            rows.append(
                {
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
                }
            )
        return rows

    def _to_result_rows(
        self,
        items: list,
        news_map: dict[tuple[str, str], str],
        trade_date: date,
    ) -> list[dict]:
        """将 NewsSentimentItem 列表转换为 AI 分析结果写入行。

        使用 (source_id, title) 复合键在 news_map 中查找，
        避免跨平台同名标题映射到同一 news_id 导致 ON CONFLICT 冲突。
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        seen_ids: set[str] = set()
        rows: list[dict] = []
        skipped_no_match = 0
        skipped_dup = 0
        for item in items:
            key = (item.source, item.title)
            news_id = news_map.get(key)
            if not news_id:
                skipped_no_match += 1
                continue
            # 去重：同一 news_id 在同一批次中只保留一条
            if news_id in seen_ids:
                skipped_dup += 1
                continue
            seen_ids.add(news_id)
            rows.append(
                {
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
                }
            )
        if skipped_no_match or skipped_dup:
            logger.warning(
                "_to_result_rows: %d 条因 news_map 无匹配跳过, %d 条因重复跳过, 最终写入 %d 条",
                skipped_no_match,
                skipped_dup,
                len(rows),
            )
        return rows

    def _to_agg_rows(self, aggregates: list) -> list[dict]:
        """将 DailySentimentAggregate 列表转换为写入行。"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = []
        for agg in aggregates:
            rows.append(
                {
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
                }
            )
        return rows

    def _db_results_to_sentiment_items(
        self,
        db_results: list,
        id_to_key: dict[str, tuple[str, str]],
        key_to_raw: dict[tuple[str, str], RawNewsItem],
    ) -> list:
        """将 AISentimentResultModel 列表转换为 NewsSentimentItem 列表。

        用于复用已分析新闻的结果，避免重复调用 LLM。

        Args:
            db_results: 已有分析结果的 ORM 模型列表。
            id_to_key: news_id → (source_id, title) 反向映射。
            key_to_raw: (source_id, title) → RawNewsItem 正向映射（用于获取来源信息）。

        Returns:
            NewsSentimentItem 列表。
        """
        from quant_etf_api.ai_factors.base import NewsSentimentItem

        now = datetime.now(timezone.utc)
        items: list[NewsSentimentItem] = []

        for row in db_results:
            key = id_to_key.get(row.news_id)
            if key is None:
                continue
            raw = key_to_raw.get(key)
            if raw is None:
                continue

            items.append(
                NewsSentimentItem(
                    timestamp=now,
                    source=raw.source_id,
                    source_name=raw.source_name,
                    title=raw.title,
                    url=raw.url,
                    asset_tags=row.asset_tags or [],
                    sentiment_score=row.sentiment_score or 0.0,
                    attention_score=row.attention_score or 0.0,
                    relevance_score=row.relevance_score or 0.0,
                    topics=row.topics or [],
                    summary=row.summary or "",
                    raw_text=raw.title,
                )
            )

        return items

    def _run_market_synthesis(
        self,
        aggregates: list,
        target_date: date,
    ) -> bool:
        """生成并持久化当日市场综合研判。

        调用 MarketSynthesisAnalyzer 生成研判，通过 MarketSynthesisRepository 写入 DB。
        所有异常内部捕获，返回 bool 表示是否成功。

        Args:
            aggregates: DailySentimentAggregate 列表。
            target_date: 交易日。

        Returns:
            True 表示研判生成并保存成功。
        """
        if not aggregates:
            logger.info("无聚合数据，跳过市场研判")
            return False

        result = self._synthesizer.generate(aggregates, target_date)
        if result is None:
            return False

        row = MarketSynthesisAnalyzer.to_db_row(result, target_date, self._client.model)
        return self._synthesis_repo.save(row)


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
