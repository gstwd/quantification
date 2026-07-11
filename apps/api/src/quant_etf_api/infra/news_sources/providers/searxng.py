"""SearXNG 新闻搜索提供者。

SearXNG — 开源元搜索引擎，支持自建实例和公共实例。
作为无配额限制的最终兜底搜索方案。
"""

from __future__ import annotations

import logging
import random
from typing import Any

import requests

from quant_etf_api.infra.news_sources.base import (
    BaseNewsSearchProvider,
    NewsSearchResponse,
    NewsSearchResult,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 20

# 已知的公共 SearXNG 实例列表（用于无自建实例时的兜底）
_PUBLIC_INSTANCES = [
    "https://searx.be",
    "https://search.sapti.me",
    "https://searx.tiekoetter.com",
]


class SearXNGProvider(BaseNewsSearchProvider):
    """SearXNG 元搜索提供者。

    特点：
    - 开源、自建，无 API 配额限制
    - 聚合多个搜索引擎结果
    - 可作为最终兜底方案

    支持自建实例和公共实例自动发现。
    """

    def __init__(
        self,
        base_urls: list[str] | None = None,
        use_public_instances: bool = False,
    ) -> None:
        """初始化 SearXNG 提供者。

        Args:
            base_urls: SearXNG 自建实例 URL 列表。
            use_public_instances: 无自建实例时是否使用公共实例（默认 False）。
        """
        urls: list[str] = []
        if base_urls:
            urls = [u.rstrip("/") for u in base_urls if u.strip()]

        if not urls and use_public_instances:
            urls = list(_PUBLIC_INSTANCES)
            random.shuffle(urls)  # 分散负载
            logger.info("SearXNG 使用公共实例模式（共 %d 个）", len(urls))

        # SearXNG 不使用传统 API Key，但用 URL 列表模拟多 Key 轮询
        super().__init__(urls, "SearXNG")

    @property
    def is_available(self) -> bool:
        """至少配置了一个实例 URL。"""
        return bool(self._api_keys)

    def _do_search(
        self,
        query: str,
        api_key: str,
        max_results: int,
        days: int = 7,
        **kwargs: Any,
    ) -> NewsSearchResponse:
        """执行 SearXNG 搜索。

        Args:
            query: 搜索关键词。
            api_key: 此处实为 SearXNG 实例 URL。
            max_results: 最大结果数。
            days: 搜索时间范围。
        """
        base_url = api_key  # api_key 在此处复用为实例 URL
        search_url = f"{base_url}/search"

        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "categories": "news",
            "language": "zh-CN",
            "pageno": 1,
        }

        try:
            resp = requests.get(
                search_url,
                params=params,
                timeout=_REQUEST_TIMEOUT,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                },
            )
            resp.raise_for_status()
            data = resp.json()

            results: list[NewsSearchResult] = []
            for item in data.get("results", [])[:max_results]:
                results.append(
                    NewsSearchResult(
                        title=item.get("title", ""),
                        snippet=(item.get("content", "") or "")[:500],
                        url=item.get("url", ""),
                        source=item.get("engine", "searxng"),
                        published_date=item.get("publishedDate"),
                    )
                )

            return NewsSearchResponse(
                query=query,
                results=results,
                provider=f"{self.name}({base_url})",
                success=True,
            )

        except requests.exceptions.Timeout:
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=f"实例 {base_url} 请求超时",
            )
        except Exception as exc:
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=str(exc),
            )
