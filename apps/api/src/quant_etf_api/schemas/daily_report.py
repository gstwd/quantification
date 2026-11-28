"""每日量化研究简报的严格内容契约与只读 API 模型。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quant_etf_api.schemas.types import UtcDatetime


class _StrictModel(BaseModel):
    """拒绝未知字段，避免报告 JSON 在前后端迭代中静默丢字段。"""

    model_config = ConfigDict(extra="forbid")


class DailyReportIndexSnapshot(_StrictModel):
    """日报中的单个指数快照。"""

    code: str
    name: str
    close: float
    change_pct: float
    trend: list[float] = Field(min_length=1)


class DailyReportMarketBreadth(_StrictModel):
    """市场广度摘要。"""

    total: int = Field(ge=0)
    advances: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    declines: int = Field(ge=0)
    advance_ratio_pct: float = Field(ge=0, le=100)
    median_change_pct: float
    limit_up: int = Field(ge=0)
    limit_down: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_counts(self) -> "DailyReportMarketBreadth":
        if self.advances + self.unchanged + self.declines != self.total:
            raise ValueError("上涨、平盘、下跌家数之和必须等于 total")
        return self


class DailyReportMarketSnapshot(_StrictModel):
    """当日市场快照。"""

    trade_date: date
    indices: list[DailyReportIndexSnapshot]
    turnover_billion: float = Field(ge=0)
    turnover_vs_20d_pct: float
    main_net_inflow_billion: float
    breadth: DailyReportMarketBreadth


class DailyReportTimelinePoint(_StrictModel):
    """连续交易日中的一行市场状态。"""

    trade_date: date
    label: str
    advance_ratio_pct: float = Field(ge=0, le=100)
    turnover_billion: float = Field(ge=0)
    csi300_pct: float
    csi1000_pct: float
    sentiment_score: float = Field(ge=0, le=100)


class DailyReportMarketSignal(_StrictModel):
    """当日资金与情绪指标。"""

    key: str
    label: str
    value: str
    delta: str
    note: str
    tone: Literal["positive", "negative", "neutral"]
    impact: Literal["bullish", "bearish", "mixed", "neutral"]
    impact_strength: Literal[1, 2, 3]
    affected_sectors: list[str] = Field(min_length=1)


class DailyReportSentimentComponent(_StrictModel):
    """单个情绪分项及其历史分位。"""

    key: str
    label: str
    display_value: str
    percentile: float = Field(ge=0, le=100)
    weight_pct: float = Field(gt=0, le=100)
    note: str


class DailyReportSentiment(_StrictModel):
    """当日市场情绪温度。"""

    score: float = Field(ge=0, le=100)
    state: Literal["freezing", "cold", "neutral", "hot", "boiling"]
    state_label: str
    summary: str
    components: list[DailyReportSentimentComponent] = Field(min_length=1)


class DailyReportAnomaly(_StrictModel):
    """单日或多日价格异动。"""

    code: str
    name: str
    asset_type: Literal["stock", "index"]
    industry: str | None = None
    themes: list[str]
    reference_name: str
    reference_change_pct: float
    relative_pct: float
    change_1d_pct: float
    change_5d_pct: float
    volume_ratio: float | None = Field(default=None, ge=0)
    turnover_rate_pct: float | None = Field(default=None, ge=0)
    anomaly_label: str
    analysis: str


class DailyReportIndustryMove(_StrictModel):
    """行业或主题的涨跌快照。"""

    code: str
    name: str
    change_pct: float
    five_day_change_pct: float
    basis: Literal["industry", "theme_index"]


class DailyReportStylePair(_StrictModel):
    """市场风格相对强弱。"""

    key: str
    left_label: str
    right_label: str
    left_return_pct: float
    right_return_pct: float
    spread_pct: float = Field(ge=0)
    leader: Literal["left", "right"]


class DailyReportMacroItem(_StrictModel):
    """宏观数据的当前值及影响。"""

    key: str
    label: str
    value: str
    previous_value: str
    delta: str
    period: str
    tone: Literal["positive", "negative", "neutral"]
    impact: Literal["bullish", "bearish", "mixed", "neutral"]
    impact_strength: Literal[1, 2, 3]
    affected_sectors: list[str] = Field(min_length=1)


class DailyReportWatchItem(_StrictModel):
    """下一交易日的验证条件。"""

    key: str
    label: str
    current_state: str
    confirm_condition: str
    invalidate_condition: str
    affected_sectors: list[str] = Field(min_length=1)


class DailyReportCalendarItem(_StrictModel):
    """已知日期或明确发布窗口的市场日程。"""

    date: str
    timing: str
    category: str
    title: str
    implication: str
    source_url: str | None = None

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value)


class DailyReportNewsItem(_StrictModel):
    """日报引用的新闻事件。"""

    id: str
    category: str
    title: str
    publisher: str
    source_tier: str
    published_at: str
    url: str
    verification: Literal["verified", "partial", "unverified", "contradicted"]
    verification_note: str
    excerpt: str
    impact: Literal["bullish", "bearish", "mixed", "neutral"]
    impact_strength: Literal[1, 2, 3]
    impact_horizon: Literal["immediate", "short", "medium"]
    affected_sectors: list[str] = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        result = _validate_http_url(value)
        if result is None:
            raise ValueError("新闻 URL 不能为空")
        return result


class DailyReportClaimCheck(_StrictModel):
    """一条市场结论与证据的交叉核验记录。"""

    claim: str
    status: Literal["supported", "partial", "unsupported", "conflicted"]
    data_evidence: str
    news_evidence: str
    assessment: str


class DailyReportInsightPoint(_StrictModel):
    """研究解读中的论点。"""

    title: str
    detail: str
    evidence_ids: list[str] = Field(min_length=1)


class DailyReportFacts(_StrictModel):
    """由数据库确定性生成、skill 不允许改写的报告事实。"""

    report_date: date
    next_trade_date: date
    data_cutoff: str
    readiness_status: Literal["ready", "partial"]
    market_snapshot: DailyReportMarketSnapshot
    timeline: list[DailyReportTimelinePoint] = Field(min_length=1)
    sentiment: DailyReportSentiment
    market_signals: list[DailyReportMarketSignal]
    stock_anomalies: list[DailyReportAnomaly]
    index_anomalies: list[DailyReportAnomaly]
    structure_basis: str
    industry_leaders: list[DailyReportIndustryMove]
    industry_laggards: list[DailyReportIndustryMove]
    style_pairs: list[DailyReportStylePair]
    macro_items: list[DailyReportMacroItem]
    warnings: list[str]


class DailyReportContent(DailyReportFacts):
    """可直接发布和由页面渲染的完整日报。"""

    schema_version: Literal["daily_report_v1"] = "daily_report_v1"
    title: str
    subtitle: str
    generated_at: UtcDatetime
    executive_summary: str
    market_regime: str
    news_items: list[DailyReportNewsItem]
    claim_checks: list[DailyReportClaimCheck]
    interpretation: list[DailyReportInsightPoint]
    risks: list[str]
    watch_items: list[DailyReportWatchItem] = Field(min_length=1, max_length=3)
    calendar_items: list[DailyReportCalendarItem] = Field(max_length=5)

    @model_validator(mode="after")
    def _validate_editorial_content(self) -> "DailyReportContent":
        news_ids = [item.id for item in self.news_items]
        if len(news_ids) != len(set(news_ids)):
            raise ValueError("news_items.id 不能重复")
        news_urls = [item.url for item in self.news_items]
        if len(news_urls) != len(set(news_urls)):
            raise ValueError("news_items.url 不能重复")
        if not self.news_items:
            raise ValueError("日报至少需要一条可核验新闻")
        if sum(item.status == "conflicted" for item in self.claim_checks) > 0:
            raise ValueError("存在 conflicted 结论，禁止发布")
        return self


class DailyReportSnapshot(_StrictModel):
    """CLI 输出的确定性事实快照与可编辑日报草稿。"""

    schema_version: Literal["daily_report_snapshot_v1"] = "daily_report_snapshot_v1"
    fact_snapshot_hash: str
    facts: DailyReportFacts
    draft: dict[str, Any]


class DailyReportSummary(_StrictModel):
    """日报列表摘要。"""

    report_id: str
    report_date: date
    revision_no: int
    status: Literal["published", "superseded"]
    title: str
    market_regime: str
    readiness_status: Literal["ready", "partial"]
    published_at: UtcDatetime


class DailyReportDetail(DailyReportSummary):
    """日报详情：摘要元数据与完整内容。"""

    content: DailyReportContent


class DailyReportListResponse(_StrictModel):
    """日报列表响应。"""

    items: list[DailyReportSummary]


def _validate_http_url(value: str | None) -> str | None:
    """校验可审计 URL 只接受 HTTP/HTTPS。"""
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL 必须是有效的 http/https 地址")
    return value
