"""申万行业轮动子系统 API 模型（调试页与只读研究端点）。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class IndustryIndexSummary(BaseModel):
    """申万一级行业摘要。"""

    industry_code: str
    name_cn: str
    is_benchmark_excluded: bool = False


class IndustryRRGPoint(BaseModel):
    """单行业单日 RRG 值。"""

    trade_date: str
    industry_code: str
    name_cn: str = ""
    rs_ratio: float | None = None
    rs_momentum: float | None = None
    quadrant: int | None = None


class IndustryRRGResponse(BaseModel):
    """RRG 序列响应。"""

    points: list[IndustryRRGPoint]
    warmup_days: int = Field(default=0, description="指标 warm-up 交易日数")


class IndustryDiffusionPoint(BaseModel):
    """单行业单日扩散指标值。"""

    trade_date: str
    industry_code: str
    name_cn: str = ""
    value: float | None = None


class IndustryDiffusionResponse(BaseModel):
    """扩散指标序列响应。"""

    points: list[IndustryDiffusionPoint]


class IndustryRotationSelection(BaseModel):
    """单日轮动选择结果。"""

    trade_date: str
    selected_codes: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)


class IndustryRotationResponse(BaseModel):
    """轮动选择结果响应。"""

    selections: list[IndustryRotationSelection]


class IndustrySummaryItem(BaseModel):
    """行业数据管理列表摘要（元数据 + 成分数 + 质量快照 + 最新行情）。"""

    industry_code: str
    name_cn: str
    is_benchmark_excluded: bool = False
    member_count: int = 0
    data_start_date: date | None = None
    data_end_date: date | None = None
    bar_count: int | None = None
    missing_day_count: int | None = None
    quality_checked_at: datetime | None = None
    latest_trade_date: date | None = None
    latest_close: float | None = None
    latest_change_pct: float | None = None


class IndustryQualityDetail(BaseModel):
    """行业详情页数据质量（快照 + 日线字段完整性）。"""

    industry_code: str
    data_start_date: date | None = None
    data_end_date: date | None = None
    bar_count: int | None = None
    missing_day_count: int | None = None
    quality_checked_at: datetime | None = None
    total: int = 0
    min_date: date | None = None
    max_date: date | None = None
    missing_open: int = 0
    missing_high: int = 0
    missing_low: int = 0
    missing_close: int = 0
    incomplete_rows: int = 0
    incomplete_ratio: float = 0.0
    change_pct_null: int = 0
    change_pct_null_rate: float = 0.0
