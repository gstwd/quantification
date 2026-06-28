"""市场日志模块请求/响应 Schema。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from quant_etf_api.schemas.types import UtcDatetime


# =============================================================================
# 标签
# =============================================================================


class TagSummary(BaseModel):
    """标签摘要（列表和关联展示用）。"""

    id: str
    name: str
    color: str
    description: str | None = None
    is_system: bool = False
    usage_count: int = 0


class TagCreate(BaseModel):
    """创建标签请求。"""

    name: str = Field(..., min_length=1, max_length=64, description="标签名称")
    color: str = Field(default="#3B82F6", max_length=7, description="十六进制颜色值")
    description: str | None = Field(default=None, max_length=256, description="标签说明")


class TagUpdate(BaseModel):
    """更新标签请求（所有字段可选）。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=7)
    description: str | None = Field(default=None, max_length=256)


class SetTagsRequest(BaseModel):
    """设置日志标签请求（全量替换）。"""

    tag_ids: list[str] = Field(default_factory=list, max_length=10)


# =============================================================================
# 指数快照
# =============================================================================


class IndexSnapshotRow(BaseModel):
    """指数快照数据行。"""

    id: str
    index_code: str
    index_name: str
    index_category: str | None = None
    sort_order: int = 0
    close_price: float | None = None
    change_pct: float | None = None
    volume_ratio_20d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    return_120d: float | None = None
    ma_20d_deviation: float | None = None
    ma_60d_deviation: float | None = None
    ma_120d_deviation: float | None = None
    volatility_20d: float | None = None
    max_drawdown_60d: float | None = None


# =============================================================================
# 手动市场数据
# =============================================================================


class JournalMarketData(BaseModel):
    """手动录入的结构化市场数据。"""

    market_up_stocks: int | None = None
    market_down_stocks: int | None = None
    market_flat_stocks: int | None = None
    limit_up_stocks: int | None = None
    limit_down_stocks: int | None = None
    total_turnover_yi: float | None = None
    turnover_vs_prev_pct: float | None = None
    north_bound_net_yi: float | None = None
    margin_balance_change_yi: float | None = None
    size_style: str | None = None
    growth_style: str | None = None
    sector_leading: str | None = None
    top_sectors: str | None = None
    bottom_sectors: str | None = None
    data_source: str | None = None
    notes: str | None = None


class JournalMarketDataUpsert(BaseModel):
    """更新市场数据请求（所有字段可选）。"""

    market_up_stocks: int | None = None
    market_down_stocks: int | None = None
    market_flat_stocks: int | None = None
    limit_up_stocks: int | None = None
    limit_down_stocks: int | None = None
    total_turnover_yi: float | None = None
    turnover_vs_prev_pct: float | None = None
    north_bound_net_yi: float | None = None
    margin_balance_change_yi: float | None = None
    size_style: str | None = None
    growth_style: str | None = None
    sector_leading: str | None = None
    top_sectors: str | None = None
    bottom_sectors: str | None = None
    data_source: str | None = None
    notes: str | None = None


# =============================================================================
# 观察分区
# =============================================================================


class ObservationRow(BaseModel):
    """观察分区内容行。"""

    id: str
    section_key: str
    section_label: str
    content: str | None = None
    sort_order: int = 0


class ObservationUpsert(BaseModel):
    """单条观察更新。"""

    section_key: str = Field(..., min_length=1, max_length=32)
    content: str | None = None


class ObservationsBatchUpdate(BaseModel):
    """批量更新观察分区请求。"""

    observations: list[ObservationUpsert] = Field(default_factory=list, max_length=10)


# =============================================================================
# AI 分析
# =============================================================================


class AIAnalysisResponse(BaseModel):
    """AI 分析结果。"""

    id: str
    model: str
    status: str
    market_summary: str | None = None
    phase_judgment: str | None = None
    style_judgment: str | None = None
    core_narrative: str | None = None
    risk_alert: str | None = None
    focus_direction: str | None = None
    error_message: str | None = None
    tokens_used: int | None = None
    created_at: UtcDatetime


# =============================================================================
# 日志条目
# =============================================================================


class JournalEntryCreate(BaseModel):
    """创建日志请求（仅需指定日期）。"""

    trade_date: date


class JournalEntryUpdate(BaseModel):
    """更新日志请求（所有字段可选，仅更新传入的字段）。"""

    market_temperature: int | None = Field(default=None, ge=0, le=100)
    profit_effect: int | None = Field(default=None, ge=0, le=100)
    risk_preference: int | None = Field(default=None, ge=0, le=100)
    trading_difficulty: int | None = Field(default=None, ge=0, le=100)
    market_consistency: int | None = Field(default=None, ge=0, le=100)
    market_phase: str | None = None
    one_line_summary: str | None = Field(default=None, max_length=256)
    is_complete: bool | None = None
    market_data: JournalMarketDataUpsert | None = None


class JournalEntrySummary(BaseModel):
    """日志摘要（列表展示用）。"""

    id: str
    trade_date: date
    market_temperature: int | None = None
    profit_effect: int | None = None
    risk_preference: int | None = None
    trading_difficulty: int | None = None
    market_consistency: int | None = None
    market_phase: str | None = None
    one_line_summary: str | None = None
    is_complete: bool = False
    word_count: int = 0
    tags: list[TagSummary] = Field(default_factory=list)
    created_at: UtcDatetime
    updated_at: UtcDatetime


class JournalEntryDetail(BaseModel):
    """日志详情（含所有关联数据）。"""

    id: str
    trade_date: date
    market_temperature: int | None = None
    profit_effect: int | None = None
    risk_preference: int | None = None
    trading_difficulty: int | None = None
    market_consistency: int | None = None
    market_phase: str | None = None
    one_line_summary: str | None = None
    is_complete: bool = False
    word_count: int = 0
    created_at: UtcDatetime
    updated_at: UtcDatetime

    index_snapshots: list[IndexSnapshotRow] = Field(default_factory=list)
    market_data: JournalMarketData | None = None
    observations: list[ObservationRow] = Field(default_factory=list)
    tags: list[TagSummary] = Field(default_factory=list)
    ai_analysis: AIAnalysisResponse | None = None


# =============================================================================
# 日历视图
# =============================================================================


class CalendarDay(BaseModel):
    """日历中的单日信息。"""

    date: date
    is_trading_day: bool
    has_entry: bool
    entry_id: str | None = None
    market_phase: str | None = None
    market_temperature: int | None = None
    tags: list[TagSummary] = Field(default_factory=list)
    one_line_summary: str | None = None


class CalendarResponse(BaseModel):
    """日历视图响应。"""

    year: int
    month: int | None = None
    days: list[CalendarDay] = Field(default_factory=list)
