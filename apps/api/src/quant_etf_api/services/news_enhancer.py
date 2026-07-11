"""新闻增强服务 — 在现有 NewsCollector 输出之上补充多源搜索新闻。

在 AIFactorService.run_full_pipeline() 中作为可选的增强步骤插入，
对现有链路是纯增量式的，不修改任何现有代码。

工作流程：
1. 从热榜新闻标题中提取高频关键词（简单分词 + TF 统计）
2. 对 Top-N 关键词通过 NewsSourceManager 发起多源搜索
3. 将搜索结果转换为 RawNewsItem 格式
4. 合并去重后返回
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from quant_etf_api.ai_factors.base import RawNewsItem
from quant_etf_api.infra.news_sources.base import NewsSearchResult
from quant_etf_api.infra.news_sources.manager import NewsSourceManager

logger = logging.getLogger(__name__)

# 中文金融高频词（用于关键词权重加成）
_FINANCE_BOOST_WORDS: frozenset[str] = frozenset({
    "A股", "股市", "大盘", "涨停", "跌停", "指数", "ETF",
    "降息", "加息", "央行", "政策", "监管", "改革",
    "业绩", "财报", "营收", "利润", "分红", "回购",
    "板块", "行业", "赛道", "龙头", "概念",
})


class NewsEnhancer:
    """新闻增强服务 — 通过多源搜索补充热点新闻。

    使用方式：在 AIFactorService.run_full_pipeline() 中，
    在 NewsCollector.fetch_all() 之后、SentimentAnalyzer.analyze_batch() 之前调用。

    Attributes:
        manager: 多源新闻搜索编排器。
        max_keywords: 从热点新闻中提取的搜索关键词数量上限。
        min_keyword_len: 最小关键词长度（字符）。
    """

    def __init__(
        self,
        manager: NewsSourceManager,
        max_keywords: int = 5,
        min_keyword_len: int = 2,
    ) -> None:
        """初始化新闻增强服务。

        Args:
            manager: NewsSourceManager 实例。
            max_keywords: 搜索关键词数量上限。
            min_keyword_len: 关键词最小长度。
        """
        self._manager = manager
        self._max_keywords = max_keywords
        self._min_keyword_len = min_keyword_len

    @property
    def is_available(self) -> bool:
        """是否有可用的新闻搜索提供者。"""
        return self._manager.is_available

    def enhance(
        self,
        hotlist_items: list[RawNewsItem],
        max_results_per_query: int = 10,
        days: int = 3,
    ) -> list[RawNewsItem]:
        """基于热榜新闻提取关键词，通过多源搜索补充相关新闻。

        流程：
        1. 从 hotlist_items 中提取高频关键词（标题分词 + TF 统计）
        2. 对 Top-N 关键词通过 NewsSourceManager 发起搜索
        3. 将搜索结果转换为 RawNewsItem 格式
        4. 合并去重后返回

        Args:
            hotlist_items: NewsCollector 输出的热榜新闻列表。
            max_results_per_query: 每个关键词的最大搜索结果数。
            days: 搜索时间范围（天）。

        Returns:
            去重后的补充新闻列表（RawNewsItem 格式）。
        """
        if not self._manager.is_available:
            logger.debug("新闻增强跳过：无可用搜索提供者")
            return []

        if not hotlist_items:
            return []

        # 1. 提取关键词
        keywords = self._extract_keywords(hotlist_items, self._max_keywords)
        if not keywords:
            logger.info("新闻增强跳过：未提取到有效关键词")
            return []

        logger.info("新闻增强: 提取到 %d 个关键词 %s", len(keywords), keywords)

        # 2. 多源搜索
        enhanced: list[RawNewsItem] = []
        seen_urls: set[str] = {item.url for item in hotlist_items if item.url}

        for kw in keywords:
            try:
                response = self._manager.search(
                    query=kw,
                    max_results=max_results_per_query,
                    days=days,
                )
                if not response.success or not response.results:
                    continue

                for result in response.results:
                    # 去重
                    if result.url and result.url in seen_urls:
                        continue
                    if result.url:
                        seen_urls.add(result.url)

                    enhanced.append(
                        self._search_result_to_raw_item(result, kw)
                    )
            except Exception:
                logger.warning("新闻增强: 关键词 '%s' 搜索失败", kw, exc_info=True)

        logger.info("新闻增强: +%d 条补充新闻", len(enhanced))
        return enhanced

    def _extract_keywords(
        self,
        items: list[RawNewsItem],
        top_n: int,
    ) -> list[str]:
        """从新闻标题中提取高频关键词。

        使用基于规则的中文分词方法（不依赖 jieba 等外部库）：
        - 按常见分隔符（空格、标点）切分
        - 提取 2-6 字的词组作为候选
        - 用 TF 排序，优先财经相关词

        Args:
            items: 热榜新闻列表。
            top_n: 返回的关键词数量上限。

        Returns:
            按优先级排序的关键词列表。
        """
        # 合并所有标题
        all_text = " ".join(item.title for item in items)

        # 简单分词：按标点和空格切分
        tokens = re.split(r"[，,。\.！!？?\s、；;：:（）()【】\[\]《》\"\"'']+", all_text)

        # 提取 2-6 字的词
        candidates: list[str] = []
        for token in tokens:
            token = token.strip()
            if self._min_keyword_len <= len(token) <= 6:
                candidates.append(token)

        if not candidates:
            return []

        # TF 统计
        counter = Counter(candidates)

        # 排序：财经词优先 × TF 降序
        def _score(word: str) -> tuple[int, int]:
            boost = 1 if word in _FINANCE_BOOST_WORDS else 0
            return (boost, counter[word])

        sorted_words = sorted(counter.keys(), key=_score, reverse=True)

        # 纯数字/符号过滤
        filtered = [
            w for w in sorted_words
            if not w.isdigit() and not all(c in ".-+×÷%" for c in w)
        ]

        return filtered[:top_n]

    @staticmethod
    def _search_result_to_raw_item(
        result: NewsSearchResult,
        keyword: str,
    ) -> RawNewsItem:
        """将 NewsSearchResult 转换为 RawNewsItem 格式。

        Args:
            result: 搜索结果。
            keyword: 搜索关键词（用于来源标识）。

        Returns:
            RawNewsItem 实例。
        """
        return RawNewsItem(
            source_id=f"search:{keyword}",
            source_name=f"搜索({result.source})",
            title=result.title,
            url=result.url,
            ranks=[],  # 搜索结果的排名不可比
            first_time=result.published_date or "",
            appear_count=1,
        )
