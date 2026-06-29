"""新闻采集器。

从 TrendRadar 项目的 crawler/fetcher.py 和 crawler/rss/ 模块提取核心能力，
重写为无状态、可测试的服务。

数据来源：
- NewsNow API（热榜平台数据）
- RSS/Atom/JSON Feed（订阅源数据）

使用示例::

    collector = NewsCollector(newsnow_api_url="https://newsnow.busiyi.world/api/s")
    items = collector.fetch_hotlist(platform_ids=["toutiao", "baidu", "weibo"])
"""

from __future__ import annotations

import logging
import time

import httpx

from quant_etf_api.ai_factors.base import RawNewsItem
from quant_etf_api.ai_factors.data.cleaner import TextCleaner

logger = logging.getLogger(__name__)

# ---- 默认热榜平台配置（来源 TrendRadar config.yaml） ----

DEFAULT_PLATFORMS: list[dict[str, str]] = [
    {"id": "toutiao", "name": "今日头条"},
    {"id": "baidu", "name": "百度热搜"},
    {"id": "wallstreetcn-hot", "name": "华尔街见闻"},
    {"id": "thepaper", "name": "澎湃新闻"},
    {"id": "bilibili-hot-search", "name": "bilibili 热搜"},
    {"id": "cls-hot", "name": "财联社热门"},
    {"id": "ifeng", "name": "凤凰网"},
    {"id": "tieba", "name": "贴吧"},
    {"id": "weibo", "name": "微博"},
    {"id": "douyin", "name": "抖音"},
    {"id": "zhihu", "name": "知乎"},
]

# 默认 RSS 源（中英文财经源，覆盖市场/金融/行业）
DEFAULT_RSS_FEEDS: list[str] = [
    # ---- 中文财经 ----
    "https://feedx.net/rss/eastmoney.xml",              # 东方财富
    "https://feedx.net/rss/21jingji.xml",                # 21 世纪经济报道
    "https://feedx.net/rss/xueqiu.xml",                  # 雪球热帖
    # ---- 综合财经（英文） ----
    "https://finance.yahoo.com/news/rssindex",           # Yahoo Finance
    "https://feeds.reuters.com/reuters/businessNews",    # Reuters Business
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrket01&id=100003114",  # CNBC Top News
    "https://feeds.marketwatch.com/marketwatch/topstories",  # MarketWatch
    "https://www.investing.com/rss/news.rss",            # Investing.com
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",  # NYT Business
    "https://feeds.bbci.co.uk/news/business/rss.xml",    # BBC Business
    "https://www.economist.com/finance-and-economics/rss.xml",  # The Economist
    # ---- 专业金融/投资 ----
    "https://seekingalpha.com/feed.xml",                 # Seeking Alpha
    "https://www.zerohedge.com/fullrss2.xml",            # ZeroHedge
    "https://feeds.feedburner.com/TheMisesInstitute",    # Mises Institute
    # ---- 加密/另类资产 ----
    "https://cointelegraph.com/rss",                     # CoinTelegraph
    "https://www.coindesk.com/arc/outboundfeeds/rss/",   # CoinDesk
    # ---- 大宗商品 ----
    "https://www.oilprice.com/rss/main",                 # OilPrice.com
]

# NewsNow API 默认地址
DEFAULT_NEWSNOW_API = "https://newsnow.busiyi.world/api/s"

# Cloudflare 需要浏览器 User-Agent，否则返回验证页面而非 JSON
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}


class NewsCollector:
    """新闻采集器，从 NewsNow API 和 RSS 源采集新闻数据。

    无状态设计：所有配置通过构造函数传入，不依赖外部文件。
    """

    def __init__(
        self,
        newsnow_api_url: str = DEFAULT_NEWSNOW_API,
        http_timeout: int = 30,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        proxy: str | None = None,
    ) -> None:
        """初始化新闻采集器。

        Args:
            newsnow_api_url: NewsNow 热榜 API 地址。
            http_timeout: HTTP 请求超时（秒）。
            max_retries: 失败重试次数。
            retry_delay: 重试间隔（秒）。
            proxy: HTTP 代理地址（可选）。
        """
        self._newsnow_url = newsnow_api_url.rstrip("/")
        self._timeout = http_timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._proxy = proxy
        self._cleaner = TextCleaner()

    # ---- 热榜采集 ----

    def fetch_hotlist(
        self,
        platform_ids: list[str] | None = None,
    ) -> list[RawNewsItem]:
        """从 NewsNow API 获取指定平台的热榜数据。

        Args:
            platform_ids: 平台 ID 列表，None 表示使用默认全部平台。

        Returns:
            RawNewsItem 列表，API 不可用时返回空列表。
        """
        if platform_ids is None:
            platform_ids = [p["id"] for p in DEFAULT_PLATFORMS]

        id_to_name = {p["id"]: p["name"] for p in DEFAULT_PLATFORMS}

        items: list[RawNewsItem] = []

        for platform_id in platform_ids:
            try:
                raw_data = self._fetch_platform(platform_id)
                if not raw_data:
                    continue

                platform_name = id_to_name.get(platform_id, platform_id)
                platform_items = self._parse_platform_data(platform_id, platform_name, raw_data)
                items.extend(platform_items)

                logger.info(
                    "热榜采集: %s (%s) → %d 条",
                    platform_name,
                    platform_id,
                    len(platform_items),
                )
            except Exception:
                logger.warning("热榜采集失败: %s", platform_id, exc_info=True)

        logger.info("热榜采集完成: %d 个平台 → %d 条新闻", len(platform_ids), len(items))
        return items

    def _fetch_platform(self, platform_id: str) -> dict | None:
        """获取单个平台的热榜数据（带重试）。"""
        url = f"{self._newsnow_url}?id={platform_id}"

        for attempt in range(self._max_retries):
            try:
                with httpx.Client(
                    timeout=self._timeout,
                    proxy=self._proxy,
                    headers=_BROWSER_HEADERS,
                ) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    return data
            except Exception as e:
                if attempt < self._max_retries - 1:
                    logger.debug("重试 %d/%d: %s", attempt + 1, self._max_retries, platform_id)
                    time.sleep(self._retry_delay)
                else:
                    logger.warning(
                        "采集失败(已重试%d次): %s, 错误: %s",
                        self._max_retries,
                        platform_id,
                        e,
                    )

        return None

    def _parse_platform_data(
        self,
        platform_id: str,
        platform_name: str,
        raw_data: dict,
    ) -> list[RawNewsItem]:
        """解析单个平台的原始 API 响应。

        NewsNow API 返回格式:
            {
                "id": "toutiao",
                "items": [
                    {
                        "title": "新闻标题",
                        "url": "https://...",
                        "mobileUrl": "...",
                        "ranks": [1, 2, ...],   # 可选
                        "count": 5,              # 可选
                    }
                ]
            }
        """
        items_data = raw_data.get("items", [])
        if not items_data:
            return []

        result: list[RawNewsItem] = []
        for item in items_data:
            if not isinstance(item, dict):
                continue

            title = self._cleaner.clean_title(item.get("title", ""))
            if not title:
                continue

            url = item.get("url", "") or item.get("mobileUrl", "")
            url = self._cleaner.normalize_url(url)

            ranks = item.get("ranks", [])
            if not isinstance(ranks, list):
                ranks = []

            result.append(
                RawNewsItem(
                    source_id=platform_id,
                    source_name=platform_name,
                    title=title,
                    url=url,
                    ranks=ranks,
                    first_time=item.get("first_time", ""),
                    last_time=item.get("last_time", ""),
                    appear_count=item.get("count", 1),
                )
            )

        return result

    # ---- RSS 采集 ----

    def fetch_rss(
        self,
        feed_urls: list[str] | None = None,
    ) -> list[RawNewsItem]:
        """从 RSS/Atom/JSON Feed 源采集新闻。

        Args:
            feed_urls: RSS feed URL 列表，None 使用默认列表。

        Returns:
            RawNewsItem 列表，无数据或解析失败时返回空列表。
        """
        if feed_urls is None:
            feed_urls = DEFAULT_RSS_FEEDS

        items: list[RawNewsItem] = []

        for url in feed_urls:
            try:
                feed_items = self._fetch_and_parse_feed(url)
                items.extend(feed_items)
                logger.info("RSS 采集: %s → %d 条", url, len(feed_items))
            except Exception:
                logger.warning("RSS 采集失败: %s", url, exc_info=True)

        logger.info("RSS 采集完成: %d 个源 → %d 条新闻", len(feed_urls), len(items))
        return items

    def _fetch_and_parse_feed(self, feed_url: str) -> list[RawNewsItem]:
        """获取并解析单个 RSS/Atom/JSON Feed。

        feedparser 为可选依赖，未安装时记录警告并返回空列表。
        """
        try:
            import feedparser
        except ImportError:
            logger.warning("feedparser 未安装，RSS 采集跳过: %s", feed_url)
            return []

        for attempt in range(self._max_retries):
            try:
                with httpx.Client(
                    timeout=self._timeout,
                    proxy=self._proxy,
                    headers=_BROWSER_HEADERS,
                ) as client:
                    resp = client.get(feed_url)
                    resp.raise_for_status()
                    content = resp.text
                    break
            except Exception as e:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay)
                else:
                    logger.warning("RSS 获取失败: %s, 错误: %s", feed_url, e)
                    return []

        feed = feedparser.parse(content)
        if feed.bozo and not feed.entries:
            logger.warning("RSS 解析失败: %s", feed_url)
            return []

        items: list[RawNewsItem] = []
        source_name = feed.feed.get("title", feed_url)

        for entry in feed.entries:
            title = self._cleaner.clean_title(entry.get("title", ""))
            if not title:
                continue

            url = entry.get("link", "")
            url = self._cleaner.normalize_url(url)
            published = entry.get("published", entry.get("updated", ""))

            items.append(
                RawNewsItem(
                    source_id=feed_url,
                    source_name=source_name,
                    title=title,
                    url=url,
                    first_time=published,
                    appear_count=1,
                )
            )

        return items

    # ---- 便捷方法 ----

    def fetch_all(
        self,
        platform_ids: list[str] | None = None,
        rss_urls: list[str] | None = None,
    ) -> list[RawNewsItem]:
        """同时采集热榜和 RSS 新闻，合并返回。

        Args:
            platform_ids: 热榜平台 ID 列表。
            rss_urls: RSS feed URL 列表。

        Returns:
            合并后的 RawNewsItem 列表（热榜在前，RSS 在后）。
        """
        hotlist = self.fetch_hotlist(platform_ids)
        rss = self.fetch_rss(rss_urls)
        return hotlist + rss
