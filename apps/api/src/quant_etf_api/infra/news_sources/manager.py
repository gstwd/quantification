"""新闻源管理器 — 多提供者编排和自动故障转移。

参考 DSA 项目的 SearchService 模式，按优先级依次尝试各搜索提供者，
一个提供者失败则自动切换到下一个。
"""

from __future__ import annotations

import logging
from typing import Any

from quant_etf_api.infra.news_sources.base import (
    BaseNewsSearchProvider,
    NewsSearchResponse,
    NewsSearchResult,
)

logger = logging.getLogger(__name__)


class NewsSourceManager:
    """多源新闻搜索编排器。

    职责：
    1. 管理多个新闻搜索提供者（按固定优先级排序）
    2. 自动故障转移：一个提供者失败则尝试下一个
    3. 提供统一 search() 接口

    优先级顺序（从高到低）：
        Anspire → Bocha → Tavily → Brave → SerpAPI → SearXNG

    使用示例::

        manager = NewsSourceManager(
            tavily_keys=["tvly-xxx"],
            bocha_keys=["bc-xxx"],
        )
        response = manager.search("A股 市场 新闻", max_results=10)
    """

    def __init__(
        self,
        tavily_keys: list[str] | None = None,
        bocha_keys: list[str] | None = None,
        brave_keys: list[str] | None = None,
        serpapi_keys: list[str] | None = None,
        anspire_keys: list[str] | None = None,
        searxng_urls: list[str] | None = None,
    ) -> None:
        """初始化新闻源管理器。

        仅激活已配置 API Key/URL 的提供者。
        未配置任何提供者时，search() 返回空结果。

        Args:
            tavily_keys: Tavily API Key 列表。
            bocha_keys: Bocha API Key 列表。
            brave_keys: Brave Search API Key 列表。
            serpapi_keys: SerpAPI Key 列表。
            anspire_keys: Anspire Search API Key 列表。
            searxng_urls: SearXNG 自建实例 URL 列表。
        """
        self._providers: list[BaseNewsSearchProvider] = []

        # 1. Anspire（最高优先级，实时智能搜索）
        if anspire_keys and any(k.strip() for k in anspire_keys):
            from quant_etf_api.infra.news_sources.providers.anspire import (
                AnspireProvider,
            )
            self._providers.append(AnspireProvider(anspire_keys))
            logger.info("已配置 Anspire 搜索（%d 个 Key）", len(anspire_keys))

        # 2. Bocha（中文搜索优化）
        if bocha_keys and any(k.strip() for k in bocha_keys):
            from quant_etf_api.infra.news_sources.providers.bocha import (
                BochaProvider,
            )
            self._providers.append(BochaProvider(bocha_keys))
            logger.info("已配置 Bocha 搜索（%d 个 Key）", len(bocha_keys))

        # 3. Tavily（AI 优化搜索）
        if tavily_keys and any(k.strip() for k in tavily_keys):
            from quant_etf_api.infra.news_sources.providers.tavily import (
                TavilyNewsProvider,
            )
            self._providers.append(TavilyNewsProvider(tavily_keys))
            logger.info("已配置 Tavily 搜索（%d 个 Key）", len(tavily_keys))

        # 4. Brave（全球覆盖）
        if brave_keys and any(k.strip() for k in brave_keys):
            from quant_etf_api.infra.news_sources.providers.brave import (
                BraveSearchProvider,
            )
            self._providers.append(BraveSearchProvider(brave_keys))
            logger.info("已配置 Brave 搜索（%d 个 Key）", len(brave_keys))

        # 5. SerpAPI（Google News）
        if serpapi_keys and any(k.strip() for k in serpapi_keys):
            from quant_etf_api.infra.news_sources.providers.serpapi import (
                SerpAPIProvider,
            )
            self._providers.append(SerpAPIProvider(serpapi_keys))
            logger.info("已配置 SerpAPI 搜索（%d 个 Key）", len(serpapi_keys))

        # 6. SearXNG（无配额兜底）
        if searxng_urls and any(u.strip() for u in searxng_urls):
            from quant_etf_api.infra.news_sources.providers.searxng import (
                SearXNGProvider,
            )
            self._providers.append(
                SearXNGProvider(base_urls=searxng_urls)
            )
            logger.info("已配置 SearXNG 搜索（%d 个实例）", len(searxng_urls))

        if not self._providers:
            logger.info("未配置任何新闻搜索提供者，多源搜索功能不可用")

    def search(
        self,
        query: str,
        max_results: int = 10,
        days: int = 7,
    ) -> NewsSearchResponse:
        """多源新闻搜索，自动故障转移。

        按优先级依次尝试各提供者，首个成功即返回。
        全部失败时返回 error_message 含所有错误信息。

        Args:
            query: 搜索关键词。
            max_results: 最大返回结果数。
            days: 搜索最近几天的内容。

        Returns:
            NewsSearchResponse 实例。
        """
        if not self._providers:
            return NewsSearchResponse(
                query=query,
                results=[],
                provider="none",
                success=False,
                error_message="未配置任何新闻搜索提供者",
            )

        errors: list[str] = []
        for provider in self._providers:
            if not provider.is_available:
                continue
            try:
                response = provider.search(query, max_results, days)
                if response.success and response.results:
                    return response
                if response.error_message:
                    errors.append(f"{provider.name}: {response.error_message}")
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                logger.warning(
                    "新闻源 %s 搜索异常: %s", provider.name, exc
                )

        return NewsSearchResponse(
            query=query,
            results=[],
            provider="none",
            success=False,
            error_message="所有新闻源均不可用: " + "; ".join(errors),
        )

    def search_multi(
        self,
        queries: list[str],
        max_results: int = 10,
        days: int = 7,
    ) -> list[NewsSearchResponse]:
        """批量搜索多个关键词。

        Args:
            queries: 搜索关键词列表。
            max_results: 每个查询的最大结果数。
            days: 搜索时间范围。

        Returns:
            按查询顺序的响应列表。
        """
        results: list[NewsSearchResponse] = []
        for query in queries:
            response = self.search(query, max_results, days)
            results.append(response)
        return results

    @property
    def available_providers(self) -> list[str]:
        """返回已激活的提供者名称列表。"""
        return [p.name for p in self._providers if p.is_available]

    @property
    def is_available(self) -> bool:
        """是否至少有一个可用的搜索提供者。"""
        return len(self.available_providers) > 0
