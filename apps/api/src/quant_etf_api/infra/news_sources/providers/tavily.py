"""Tavily 新闻搜索提供者。

封装 Tavily Search API（专为 AI/LLM 优化的搜索服务）。
需要安装 tavily-python 可选依赖。
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


class TavilyNewsProvider(BaseNewsSearchProvider):
    """Tavily 搜索引擎提供者。

    特点：
    - 专为 AI/LLM 优化的搜索 API
    - 免费版每月 1000 次请求
    - 支持高级搜索深度和新闻主题过滤

    文档：https://docs.tavily.com/
    """

    def __init__(self, api_keys: list[str]) -> None:
        """初始化 Tavily 提供者。

        Args:
            api_keys: Tavily API Key 列表。
        """
        super().__init__(api_keys, "Tavily")

    def _do_search(
        self,
        query: str,
        api_key: str,
        max_results: int,
        days: int = 7,
        **kwargs: Any,
    ) -> NewsSearchResponse:
        """执行 Tavily 搜索。"""
        try:
            from tavily import TavilyClient
        except ImportError:
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="tavily-python 未安装，请运行: pip install tavily-python",
            )

        try:
            client = TavilyClient(api_key=api_key)

            search_kwargs: dict[str, Any] = {
                "query": query,
                "search_depth": "advanced",
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
                "days": days,
            }
            response = client.search(**search_kwargs)

            results: list[NewsSearchResult] = []
            for item in response.get("results", []):
                results.append(
                    NewsSearchResult(
                        title=item.get("title", ""),
                        snippet=(item.get("content", "") or "")[:500],
                        url=item.get("url", ""),
                        source=_extract_domain(item.get("url", "")),
                        published_date=item.get("published_date"),
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
            if "rate limit" in error_msg.lower() or "quota" in error_msg.lower():
                error_msg = f"API 配额已用尽: {error_msg}"
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg,
            )


def _extract_domain(url: str) -> str:
    """从 URL 提取域名作为来源标识。"""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        return domain or "未知来源"
    except Exception:
        return "未知来源"
