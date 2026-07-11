"""社交媒体情绪服务 — Reddit / X / Polymarket 情绪数据。

通过 api.adanos.org 获取美股社交媒体情绪数据。
可选功能，仅当配置 SOCIAL_SENTIMENT_API_KEY 时激活。
当前仅对美股代码生效，为未来 US ETF 支持做准备。

参考 DSA 项目的 SocialSentimentService 设计。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_TRANSIENT_EXCEPTIONS = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

_REQUEST_TIMEOUT = 8
_REQUEST_RETRY_ATTEMPTS = 2
_CACHE_TTL_SECONDS = 600


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type(_TRANSIENT_EXCEPTIONS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _get_with_retry(
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    timeout: int = _REQUEST_TIMEOUT,
) -> requests.Response:
    """带重试的 HTTP GET 请求。"""
    return requests.get(url, headers=headers, params=params or {}, timeout=timeout)


class SocialSentimentService:
    """社交媒体情绪服务。

    通过 api.adanos.org 获取 Reddit / X / Polymarket 情绪数据。
    可选功能，仅当配置 API Key 时激活。

    使用示例::

        svc = SocialSentimentService(api_key="sk_live_xxx")
        if svc.is_available:
            context = svc.get_sentiment_context("SPY")
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str = "https://api.adanos.org",
    ) -> None:
        """初始化社交媒体情绪服务。

        Args:
            api_key: api.adanos.org 的 API Key。
            api_url: API 基础 URL。
        """
        self._api_key = (api_key or "").strip()
        self._api_url = api_url.rstrip("/")
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_lock = threading.RLock()
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.RLock()

    @property
    def is_available(self) -> bool:
        """是否配置了有效的 API Key。"""
        return bool(self._api_key)

    def get_sentiment_context(self, ticker: str) -> str | None:
        """获取指定美股代码的社交媒体情绪上下文文本。

        Args:
            ticker: 美股代码（如 "SPY"、"AAPL"）。

        Returns:
            格式化的情绪分析文本（用于注入 LLM prompt），不可用时返回 None。
        """
        if not self.is_available:
            return None

        all_data = self._fetch_all(ticker)
        if not all_data:
            return None

        return self._format_social_intel(ticker, all_data)

    def _fetch_all(self, ticker: str) -> dict[str, Any] | None:
        """获取指定美股代码的全部社交媒体数据（带缓存）。"""
        ticker = ticker.strip().upper()
        if not ticker:
            return None

        # 检查缓存
        with self._cache_lock:
            if ticker in self._cache:
                ts, data = self._cache[ticker]
                if time.time() - ts < _CACHE_TTL_SECONDS:
                    return data

        # 去重并发请求
        with self._inflight_lock:
            if ticker in self._inflight:
                event = self._inflight[ticker]
            else:
                event = threading.Event()
                self._inflight[ticker] = event

        if not event.is_set():
            try:
                headers = {"Authorization": f"Bearer {self._api_key}"}
                params = {"ticker": ticker}
                resp = _get_with_retry(
                    f"{self._api_url}/v1/social/sentiment",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

                with self._cache_lock:
                    self._cache[ticker] = (time.time(), data)

                return data
            except Exception:
                logger.warning("社交媒体情绪获取失败: %s", ticker, exc_info=True)
                return None
            finally:
                event.set()
                with self._inflight_lock:
                    self._inflight.pop(ticker, None)

        # 等待并发请求完成
        event.wait()
        with self._cache_lock:
            cached = self._cache.get(ticker)
            if cached:
                return cached[1]
        return None

    @staticmethod
    def _format_social_intel(ticker: str, data: dict[str, Any]) -> str:
        """格式化社交媒体数据为 prompt 可注入的文本。"""
        lines = [f"## 社交媒体情绪 ({ticker})"]

        reddit = data.get("reddit", {})
        if reddit:
            lines.append(
                f"- Reddit: 提及 {reddit.get('mentions', 'N/A')} 次，"
                f"情绪 {reddit.get('sentiment', 'N/A')}"
            )

        twitter = data.get("twitter", data.get("x", {}))
        if twitter:
            lines.append(
                f"- X/Twitter: 提及 {twitter.get('mentions', 'N/A')} 次，"
                f"情绪 {twitter.get('sentiment', 'N/A')}"
            )

        polymarket = data.get("polymarket", {})
        if polymarket:
            lines.append(
                f"- Polymarket: {polymarket.get('summary', 'N/A')}"
            )

        return "\n".join(lines)
