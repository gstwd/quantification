"""SerpAPI 新闻搜索提供者。

SerpAPI — 聚合 Google/Bing/Baidu 等多个搜索引擎的结构化结果。
使用 Google News 引擎进行新闻搜索。
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


class SerpAPIProvider(BaseNewsSearchProvider):
    """SerpAPI 搜索提供者。

    特点：
    - 支持 Google News/Bing/Baidu 等多个搜索引擎
    - 免费版每月 100 次请求
    - 返回结构化的搜索结果

    文档：https://serpapi.com/
    """

    def __init__(self, api_keys: list[str]) -> None:
        """初始化 SerpAPI 提供者。

        Args:
            api_keys: SerpAPI Key 列表。
        """
        super().__init__(api_keys, "SerpAPI")

    def _do_search(
        self,
        query: str,
        api_key: str,
        max_results: int,
        days: int = 7,  # noqa: ARG002 — 保留接口一致性
        **kwargs: Any,  # noqa: ARG002 — 保留接口一致性
    ) -> NewsSearchResponse:
        """执行 SerpAPI Google News 搜索。"""
        try:
            from serpapi import GoogleSearch
        except ImportError:
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=(
                    "google-search-results 未安装，请运行: "
                    "pip install google-search-results"
                ),
            )

        try:
            params: dict[str, Any] = {
                "engine": "google_news",
                "q": query,
                "api_key": api_key,
                "gl": "cn",
                "hl": "zh-cn",
                "num": min(max_results, 20),
            }

            search = GoogleSearch(params)
            response = search.get_dict()

            results: list[NewsSearchResult] = []
            news_results = response.get("news_results", [])
            for item in news_results:
                results.append(
                    NewsSearchResult(
                        title=item.get("title", ""),
                        snippet=(item.get("snippet", "") or "")[:500],
                        url=item.get("link", ""),
                        source=item.get("source", {}).get("name", "Google News"),
                        published_date=item.get("date"),
                    )
                )

            return NewsSearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )

        except Exception as exc:
            error_msg = str(exc)
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg,
            )
