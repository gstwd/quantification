"""Bocha AI 新闻搜索提供者。

Bocha（博查）搜索 API，专注于中文金融新闻搜索优化。
使用 HTTP POST 调用 Bocha AI Search 端点。
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

_DEFAULT_BOCHA_URL = "https://api.bocha.cn/v1/ai/search"
_REQUEST_TIMEOUT = 15


class BochaProvider(BaseNewsSearchProvider):
    """Bocha（博查）AI 搜索提供者。

    特点：
    - 中文搜索优化，金融新闻质量高
    - 支持 AI 摘要
    - 适合 A 股市场舆情分析

    文档：https://open.bocha.cn/
    """

    def __init__(
        self,
        api_keys: list[str],
        api_url: str = _DEFAULT_BOCHA_URL,
    ) -> None:
        """初始化 Bocha 提供者。

        Args:
            api_keys: Bocha API Key 列表。
            api_url: Bocha API 端点（可自定义）。
        """
        super().__init__(api_keys, "Bocha")
        self._api_url = api_url

    def _do_search(
        self,
        query: str,
        api_key: str,
        max_results: int,
        days: int = 7,
        **kwargs: Any,
    ) -> NewsSearchResponse:
        """执行 Bocha 搜索。"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "query": query,
            "freshness": "noLimit",
            "count": min(max_results, 10),
        }

        try:
            resp = requests.post(
                self._api_url,
                json=payload,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            results: list[NewsSearchResult] = []
            for item in data.get("data", {}).get("webPages", {}).get("value", []):
                results.append(
                    NewsSearchResult(
                        title=item.get("name", ""),
                        snippet=(item.get("snippet", "") or "")[:500],
                        url=item.get("url", ""),
                        source=item.get("displayUrl", "bocha"),
                        published_date=item.get("dateLastCrawled"),
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
