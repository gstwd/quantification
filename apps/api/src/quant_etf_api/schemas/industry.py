"""申万行业轮动子系统 API 模型（调试页与只读研究端点）。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from quant_etf_api.schemas.types import UtcDatetime


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


class IndustryLabIssue(BaseModel):
    """调试页数据问题条目（替代“空结果=无数据”的静默语义）。"""

    level: str = "warn"
    code: str
    message: str
    industry_codes: list[str] = Field(default_factory=list)
    count: int | None = None
    sample_dates: list[str] = Field(default_factory=list)


class IndustryRRGCoverageItem(BaseModel):
    """单行业 RRG 数据覆盖与有效区间（按行业日线质量统计）。"""

    industry_code: str
    name_cn: str = ""
    data_start_date: date | None = None
    data_end_date: date | None = None
    expected_input_days: int = 0
    present_input_days: int = 0
    missing_input_days: int = 0
    output_days: int = 0
    leading_nan_days: int = 0
    valid_count: int = 0
    valid_from: date | None = None
    valid_until: date | None = None


class IndustryRRGMeta(BaseModel):
    """RRG 查询的计算口径与数据问题汇总。"""

    requested_start: date
    requested_end: date
    effective_start: date | None = None
    effective_end: date | None = None
    market_trading_days: int = 0
    warmup_required: int = 0
    max_range_days: int = 0
    range_exceeded: bool = False
    issues: list[IndustryLabIssue] = Field(default_factory=list)
    coverage: list[IndustryRRGCoverageItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IndustryRRGResponse(BaseModel):
    """RRG 序列响应。"""

    points: list[IndustryRRGPoint]
    warmup_days: int = Field(default=0, description="指标 warm-up 交易日数")
    meta: IndustryRRGMeta | None = None


class IndustryIndexCorrelationItem(BaseModel):
    """一个行业与指定指数的日收益相关度。"""

    industry_code: str
    name_cn: str = ""
    correlation: float | None = None
    sample_count: int = 0


class IndustryIndexCorrelationResponse(BaseModel):
    """指定指数与多个行业的相关度查询响应。"""

    index_code: str
    start: date
    end: date
    index_close_days: int = 0
    items: list[IndustryIndexCorrelationItem] = Field(default_factory=list)


class IndustryDiffusionPoint(BaseModel):
    """单行业单日扩散指标值。"""

    trade_date: str
    industry_code: str
    name_cn: str = ""
    value: float | None = None
    valid_count: int | None = None
    member_count: int | None = None
    coverage: float | None = None


class IndustryDiffusionCoverageItem(BaseModel):
    """单行业扩散数据覆盖统计（成员股、样本、无样本日）。"""

    industry_code: str
    name_cn: str = ""
    member_count: int = 0
    data_start_date: date | None = None
    data_end_date: date | None = None
    output_days: int = 0
    no_sample_days: int = 0
    valid_count: int = 0
    valid_from: date | None = None
    valid_until: date | None = None
    avg_sample_count: float | None = None


class IndustryDiffusionMeta(BaseModel):
    """扩散查询的计算口径与数据问题汇总。"""

    requested_start: date
    requested_end: date
    effective_start: date | None = None
    effective_end: date | None = None
    market_trading_days: int = 0
    output_days: int = 0
    max_range_days: int = 0
    range_exceeded: bool = False
    issues: list[IndustryLabIssue] = Field(default_factory=list)
    coverage: list[IndustryDiffusionCoverageItem] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)


class IndustryDiffusionResponse(BaseModel):
    """扩散指标序列响应。"""

    points: list[IndustryDiffusionPoint]
    meta: IndustryDiffusionMeta | None = None


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
    quality_checked_at: UtcDatetime | None = None
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
    quality_checked_at: UtcDatetime | None = None
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
