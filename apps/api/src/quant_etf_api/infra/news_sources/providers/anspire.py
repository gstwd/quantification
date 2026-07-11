"""Anspire 新闻搜索提供者。

Anspire Search — 实时智能搜索优化，汇聚全球舆情信息。
适配 A 股、美股、港股等新闻和舆情检索。
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

_DEFAULT_ANSPIRE_URL = "https://open.anspire.cn/api/search"
_REQUEST_TIMEOUT = 20


class AnspireProvider(BaseNewsSearchProvider):
    """Anspire Search 提供者。

    特点：
    - 实时智能搜索，汇聚全球舆情
    - 适配 A 股、美股、港股新闻检索
    - 高优先级推荐提供者

    文档：https://open.anspire.cn/
    """

    def __init__(
        self,
        api_keys: list[str],
        api_url: str = _DEFAULT_ANSPIRE_URL,
    ) -> None:
        """初始化 Anspire 提供者。

        Args:
            api_keys: Anspire API Key 列表。
            api_url: API 端点（可自定义）。
        """
        super().__init__(api_keys, "Anspire")
        self._api_url = api_url

    def _do_search(
        self,
        query: str,
        api_key: str,
        max_results: int,
        days: int = 7,
        **kwargs: Any,
    ) -> NewsSearchResponse:
        """执行 Anspire 搜索。"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "query": query,
            "max_results": min(max_results, 20),
            "days": days,
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
            items = data.get("results", data.get("data", []))
            if isinstance(items, dict):
                items = items.get("items", [])
            for item in items:
                if isinstance(item, dict):
                    results.append(
                        NewsSearchResult(
                            title=item.get("title", ""),
                            snippet=(item.get("snippet", item.get("content", "")) or "")[
                                :500
                            ],
                            url=item.get("url", item.get("link", "")),
                            source=item.get("source", "anspire"),
                            published_date=item.get("published_date", item.get("date")),
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
