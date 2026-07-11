"""Brave Search 新闻提供者。

Brave Search API — 隐私优先的搜索引擎，支持全球金融新闻。
使用 HTTP GET + Bearer Token 认证。
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from quant_etf_api.infra.news_sources.base import (
    BaseNewsSearchProvider,
    NewsSearchResponse,
    NewsSearchResult,
)

logger = logging.getLogger(__name__)

_DEFAULT_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_REQUEST_TIMEOUT = 15


class BraveSearchProvider(BaseNewsSearchProvider):
    """Brave Search API 提供者。

    特点：
    - 隐私优先，不追踪用户
    - 覆盖全球新闻源
    - 免费版每月 2000 次查询

    文档：https://brave.com/search/api/
    """

    def __init__(
        self,
        api_keys: list[str],
        api_url: str = _DEFAULT_BRAVE_URL,
    ) -> None:
        """初始化 Brave 提供者。

        Args:
            api_keys: Brave Search API Key 列表。
            api_url: API 端点（可自定义）。
        """
        super().__init__(api_keys, "Brave")
        self._api_url = api_url

    def _do_search(
        self,
        query: str,
        api_key: str,
        max_results: int,
        days: int = 7,
        **kwargs: Any,
    ) -> NewsSearchResponse:
        """执行 Brave 搜索。"""
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        }
        params: dict[str, Any] = {
            "q": query,
            "count": min(max_results, 20),
            "freshness": "pw" if days <= 7 else "pm",  # past week / past month
        }

        try:
            resp = requests.get(
                self._api_url,
                headers=headers,
                params=params,
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            results: list[NewsSearchResult] = []
            web_results = data.get("web", {}).get("results", [])
            for item in web_results:
                results.append(
                    NewsSearchResult(
                        title=item.get("title", ""),
                        snippet=(item.get("description", "") or "")[:500],
                        url=item.get("url", ""),
                        source=_extract_brave_source(item),
                        published_date=item.get("age"),
                    )
                )

            return NewsSearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )

        except requests.exceptions.Timeout:
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="请求超时",
            )
        except Exception as exc:
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=str(exc),
            )


def _extract_brave_source(item: dict) -> str:
    """从 Brave 搜索结果中提取来源信息。"""
    profile = item.get("profile", {})
    if profile:
        name = profile.get("name", "")
        if name:
            return name
    # 从 URL 提取域名
    url = item.get("url", "")
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return "未知来源"
