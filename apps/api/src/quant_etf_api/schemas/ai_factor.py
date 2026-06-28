"""AI 因子 API 的 Pydantic schema。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class NewsItemResponse(BaseModel):
    """新闻条目 API 响应。"""

    id: str = Field(description="新闻唯一标识")
    source_id: str = Field(description="来源平台 ID")
    source_name: str = Field(default="", description="来源平台中文名")
    title: str = Field(description="新闻标题")
    url: str = Field(default="", description="新闻链接")
    rank: int | None = Field(default=None, description="热榜排名")
    crawl_date: date = Field(description="采集日期")
    appear_count: int = Field(default=1, description="出现次数")


class SentimentResultResponse(BaseModel):
    """AI 情绪分析结果 API 响应。"""

    id: str = Field(description="分析结果 ID")
    news_id: str = Field(description="关联新闻 ID")
    trade_date: date = Field(description="关联交易日")
    asset_tags: list[str] = Field(default_factory=list, description="资产标签")
    topics: list[str] = Field(default_factory=list, description="主题标签")
    sentiment_score: float | None = Field(default=None, description="情绪分")
    attention_score: float | None = Field(default=None, description="关注度分")
    relevance_score: float | None = Field(default=None, description="市场相关度")
    summary: str = Field(default="", description="AI 摘要")
    llm_model: str = Field(default="", description="LLM 模型")


class DailySentimentResponse(BaseModel):
    """每日情绪聚合 API 响应。"""

    trade_date: date = Field(description="交易日")
    asset_tag: str = Field(description="资产标签")
    avg_sentiment: float = Field(default=0.0, description="平均情绪分")
    weighted_sentiment: float = Field(default=0.0, description="加权情绪分")
    total_attention: float = Field(default=0.0, description="总关注度")
    news_count: int = Field(default=0, description="新闻数")
    top_topics: list[str] = Field(default_factory=list, description="Top 主题")
    positive_ratio: float = Field(default=0.0, description="正面占比")
    negative_ratio: float = Field(default=0.0, description="负面占比")


class AIAnalysisRunResponse(BaseModel):
    """AI 分析执行结果。"""

    status: str = Field(description="执行状态: success / failed / accepted")
    collected: int = Field(default=0, description="采集新闻数")
    saved: int = Field(default=0, description="存储新增数")
    analyzed: int = Field(default=0, description="AI 分析数")
    aggregated: int = Field(default=0, description="聚合组数")
    error: str | None = Field(default=None, description="错误信息")
    run_id: str | None = Field(default=None, description="运行 ID（异步模式时返回）")
