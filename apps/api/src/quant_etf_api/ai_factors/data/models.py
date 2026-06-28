"""数据采集层的纯 Python 数据模型。

这些 dataclass 用于在模块间传递数据，与数据库 ORM 模型分离。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class NewsItemRecord:
    """原始新闻记录（对应 news_item 表结构）。

    Attributes:
        source_id: 平台 ID。
        source_name: 平台中文名。
        title: 新闻标题。
        url: 新闻链接。
        rank: 热榜排名（1=榜首）。
        first_seen_at: 首次出现时间。
        last_seen_at: 最后出现时间。
        appear_count: 出现次数。
        raw_payload: 原始 API 返回数据。
    """

    source_id: str
    source_name: str
    title: str
    url: str = ""
    rank: int | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    appear_count: int = 1
    raw_payload: dict | None = None


@dataclass
class SentimentResultRecord:
    """AI 情绪分析结果记录（对应 ai_sentiment_result 表结构）。

    Attributes:
        news_id: 关联新闻 ID。
        trade_date: 关联交易日。
        asset_tags: 资产标签 JSON 数组。
        topics: 主题标签 JSON 数组。
        sentiment_score: 情绪分。
        attention_score: 关注度分。
        relevance_score: 市场相关度。
        summary: AI 生成摘要。
        llm_model: 使用的 LLM 模型。
        llm_response: 完整 LLM 响应。
    """

    news_id: str
    trade_date: date
    asset_tags: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0
    attention_score: float = 0.0
    relevance_score: float = 0.0
    summary: str = ""
    llm_model: str = ""
    llm_response: dict | None = None


@dataclass
class DailyAggregateRecord:
    """每日情绪聚合记录（对应 daily_sentiment_aggregate 表结构）。

    Attributes:
        trade_date: 交易日。
        asset_tag: 资产标签。
        avg_sentiment: 平均情绪分。
        weighted_sentiment: 关注度加权情绪分。
        total_attention: 总关注度。
        news_count: 新闻数。
        top_topics: Top 主题。
        positive_ratio: 正面占比。
        negative_ratio: 负面占比。
    """

    trade_date: date
    asset_tag: str
    avg_sentiment: float = 0.0
    weighted_sentiment: float = 0.0
    total_attention: float = 0.0
    news_count: int = 0
    top_topics: list[str] = field(default_factory=list)
    positive_ratio: float = 0.0
    negative_ratio: float = 0.0
