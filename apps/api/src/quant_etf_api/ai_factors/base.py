"""AI 因子层统一数据结构和基础模型。

定义了 AI 因子层所有模块共享的数据类型，是整个 AI 因子层的"合约"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class RawNewsItem:
    """原始新闻条目（采集阶段输出）。

    从 NewsNow API 或 RSS feed 采集后直接生成的中间数据结构，
    尚未经过 AI 分析。

    Attributes:
        source_id: 来源平台 ID（如 toutiao、baidu、weibo）。
        source_name: 来源平台中文名（如今日头条、百度热搜）。
        title: 新闻标题。
        url: 新闻链接。
        ranks: 历史排名列表（1=榜首）。
        first_time: 首次上榜时间。
        last_time: 最后上榜时间。
        appear_count: 出现次数。
    """

    source_id: str
    source_name: str
    title: str
    url: str = ""
    ranks: list[int] = field(default_factory=list)
    first_time: str = ""
    last_time: str = ""
    appear_count: int = 1


@dataclass
class NewsSentimentItem:
    """单条新闻的 AI 情绪分析结果。

    由 SentimentAnalyzer 调用 LLM 后生成，是 AI 分析的核心输出。
    所有字段必须填充，不可为空。

    Attributes:
        timestamp: 分析时间戳。
        source: 来源平台 ID。
        source_name: 来源平台中文名。
        title: 新闻标题（已清洗）。
        url: 新闻链接（已规范化）。
        asset_tags: 关联资产标签列表（指数代码或行业标签）。
        sentiment_score: 情绪分 [-1.0, 1.0]，正=利好，负=利空。
        attention_score: 关注度分 [0, 100]，基于排名+频次计算。
        relevance_score: A 股市场相关度 [0, 1]。
        topics: 主题标签列表（如 ["AI", "芯片", "新能源"]）。
        summary: AI 生成摘要（50 字以内）。
        raw_text: 原始标题文本（用于调试）。
    """

    timestamp: datetime
    source: str
    source_name: str
    title: str
    url: str = ""
    asset_tags: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0
    attention_score: float = 0.0
    relevance_score: float = 0.0
    topics: list[str] = field(default_factory=list)
    summary: str = ""
    raw_text: str = ""


@dataclass
class DailySentimentAggregate:
    """每日情绪聚合结果。

    由 TrendScorer 按 asset_tag 对当日所有 NewsSentimentItem 聚合生成，
    每条记录对应一个 asset_tag 在某一交易日的汇总数据。

    Attributes:
        date: 交易日。
        asset_tag: 关联标签（指数代码如 "000300"，或行业如 "科技"）。
        avg_sentiment: 平均情绪分。
        weighted_sentiment: 关注度加权情绪分（高关注度新闻权重更大）。
        total_attention: 总关注度。
        news_count: 相关新闻数量。
        top_topics: Top 主题列表。
        positive_ratio: 正面新闻占比（sentiment > 0.15）。
        negative_ratio: 负面新闻占比（sentiment < -0.15）。
    """

    date: date
    asset_tag: str
    avg_sentiment: float = 0.0
    weighted_sentiment: float = 0.0
    total_attention: float = 0.0
    news_count: int = 0
    top_topics: list[str] = field(default_factory=list)
    positive_ratio: float = 0.0
    negative_ratio: float = 0.0


# ---- 预定义标签体系 ----

# 指数标签（与 benchmark_index 表中的 index_code 对齐）
INDEX_TAGS: dict[str, str] = {
    "000300": "沪深300",
    "000905": "中证500",
    "000016": "上证50",
    "399006": "创业板指",
    "000688": "科创50",
    "000852": "中证1000",
    "399673": "创业板50",
}

# 行业标签（申万一级行业 + 常见分类）
SECTOR_TAGS: list[str] = [
    "金融",
    "科技",
    "消费",
    "医药",
    "新能源",
    "半导体",
    "军工",
    "地产",
    "有色",
    "化工",
    "汽车",
    "传媒",
    "通信",
    "农业",
    "基建",
]

# 概念/主题标签
CONCEPT_TAGS: list[str] = [
    "人工智能",
    "AI",
    "芯片",
    "新能源车",
    "光伏",
    "数字经济",
    "机器人",
    "低空经济",
    "量子计算",
    "人形机器人",
    "自动驾驶",
    "储能",
    "氢能",
    "元宇宙",
    "Web3",
    "央企改革",
    "一带一路",
    "碳中和",
]

# 合并所有可用标签
ALL_AVAILABLE_TAGS: list[str] = list(INDEX_TAGS.keys()) + SECTOR_TAGS + CONCEPT_TAGS
