"""新闻多源搜索框架。

提供：
- BaseNewsSearchProvider：新闻搜索提供者抽象基类
- NewsSearchResult / NewsSearchResponse：搜索数据结构
- NewsSourceManager：多源编排器，优先级 failover
- 6 个搜索提供者实现：Anspire / Bocha / Tavily / Brave / SerpAPI / SearXNG

所有提供者均为可选，仅在配置了对应 API Key 时激活。
"""

from __future__ import annotations

from quant_etf_api.infra.news_sources.base import (
    BaseNewsSearchProvider,
    NewsSearchResponse,
    NewsSearchResult,
)

__all__ = [
    "BaseNewsSearchProvider",
    "NewsSearchResponse",
    "NewsSearchResult",
]
