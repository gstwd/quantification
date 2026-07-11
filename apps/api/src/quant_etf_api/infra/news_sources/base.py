"""新闻搜索提供者抽象基类。

参考 DSA 项目的 BaseSearchProvider 设计，简化为适合 ETF 项目需求
的版本。提供多 API Key 轮询负载均衡和错误跟踪能力。
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from itertools import cycle
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class NewsSearchResult:
    """新闻搜索结果。

    Attributes:
        title: 新闻标题。
        snippet: 内容摘要（截取前 500 字符）。
        url: 新闻链接。
        source: 来源网站/域名。
        published_date: 发布日期（可选）。
        relevance_score: 相关性分数 0-100（可选）。
    """

    title: str
    snippet: str
    url: str
    source: str
    published_date: str | None = None
    relevance_score: float | None = None


@dataclass
class NewsSearchResponse:
    """新闻搜索响应。

    Attributes:
        query: 搜索关键词。
        results: 搜索结果列表。
        provider: 实际使用的搜索提供者名称。
        success: 是否成功。
        error_message: 失败时的错误信息。
        search_time: 搜索耗时（秒）。
    """

    query: str
    results: list[NewsSearchResult] = field(default_factory=list)
    provider: str = ""
    success: bool = True
    error_message: str | None = None
    search_time: float = 0.0


class BaseNewsSearchProvider(ABC):
    """新闻搜索提供者抽象基类。

    子类需实现：
    - name 属性
    - _do_search(query, api_key, max_results, days) 方法

    基类提供：
    - 多 API Key 轮询负载均衡（round-robin）
    - Key 级别的错误计数和自动跳过（连续失败 >3 次的 Key 暂时跳过）
    - 统一的 search() 入口和错误处理
    """

    def __init__(self, api_keys: list[str], name: str) -> None:
        """初始化搜索提供者。

        Args:
            api_keys: API Key 列表（支持多个 Key 做负载均衡）。
            name: 提供者名称（如 "Tavily"）。
        """
        self._api_keys = [k.strip() for k in api_keys if k.strip()]
        self._name = name
        self._key_cycle = cycle(self._api_keys) if self._api_keys else None
        self._key_usage: dict[str, int] = {}
        self._key_errors: dict[str, int] = {k: 0 for k in self._api_keys}
        self._state_lock = threading.RLock()

    @property
    def name(self) -> str:
        """返回提供者名称。"""
        return self._name

    @property
    def is_available(self) -> bool:
        """检查是否有可用的 API Key。"""
        return bool(self._api_keys)

    @abstractmethod
    def _do_search(
        self,
        query: str,
        api_key: str,
        max_results: int,
        days: int = 7,
        **kwargs: Any,
    ) -> NewsSearchResponse:
        """执行搜索（子类实现）。

        Args:
            query: 搜索关键词。
            api_key: 当前轮到的 API Key。
            max_results: 最大返回结果数。
            days: 搜索最近几天的内容。
            **kwargs: 提供者特定的额外参数。

        Returns:
            NewsSearchResponse 实例。
        """
        ...

    def search(
        self,
        query: str,
        max_results: int = 5,
        days: int = 7,
        **kwargs: Any,
    ) -> NewsSearchResponse:
        """执行搜索（统一入口，处理多 Key 轮询和错误记录）。

        Args:
            query: 搜索关键词。
            max_results: 最大返回结果数。
            days: 搜索最近几天的内容。
            **kwargs: 传递给 _do_search 的额外参数。

        Returns:
            NewsSearchResponse 实例。
        """
        api_key = self._get_next_key()
        if not api_key:
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message=f"{self._name} 未配置 API Key",
            )

        start_time = time.time()
        try:
            response = self._do_search(
                query, api_key, max_results, days=days, **kwargs
            )
            response.search_time = time.time() - start_time

            if response.success:
                self._record_success(api_key)
                logger.info(
                    "[%s] 搜索 '%s' 成功，返回 %d 条结果，耗时 %.2fs",
                    self._name,
                    query,
                    len(response.results),
                    response.search_time,
                )
            else:
                self._record_error(api_key)

            return response

        except Exception as exc:
            self._record_error(api_key)
            elapsed = time.time() - start_time
            logger.error("[%s] 搜索 '%s' 失败: %s", self._name, query, exc)
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message=str(exc),
                search_time=elapsed,
            )

    def _get_next_key(self) -> str | None:
        """轮询获取下一个可用的 API Key（跳过错误过多的 key）。

        策略：round-robin，连续错误 >3 次的 Key 暂时跳过。
        所有 Key 都有问题时重置错误计数。

        Returns:
            API Key 字符串或 None。
        """
        with self._state_lock:
            if not self._key_cycle:
                return None

            for _ in range(len(self._api_keys)):
                key = next(self._key_cycle)
                if self._key_errors.get(key, 0) < 3:
                    return key

            # 所有 Key 都有问题，重置错误计数
            logger.warning("[%s] 所有 API Key 都有错误记录，重置错误计数", self._name)
            self._key_errors = {k: 0 for k in self._api_keys}
            return self._api_keys[0] if self._api_keys else None

    def _record_success(self, key: str) -> None:
        """记录一次成功使用，递减错误计数。"""
        with self._state_lock:
            self._key_usage[key] = self._key_usage.get(key, 0) + 1
            if key in self._key_errors and self._key_errors[key] > 0:
                self._key_errors[key] -= 1

    def _record_error(self, key: str) -> None:
        """记录一次错误，递增错误计数。"""
        with self._state_lock:
            self._key_errors[key] = self._key_errors.get(key, 0) + 1
