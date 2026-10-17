"""量价 / 择时研报研究台 API 模型（只读研究端点）。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class SignalLabParam(BaseModel):
    """研究参数声明。"""

    name: str
    label: str
    default: float
    minimum: float
    maximum: float
    kind: str = "int"


class SignalLabReferenceLine(BaseModel):
    """信号值轴参考线。"""

    label: str
    value: float


class SignalLabDefinition(BaseModel):
    """因子 / 信号定义（含公式与原理说明）。"""

    key: str
    label: str
    source: str
    formula: str
    principle: str
    required_fields: list[str] = Field(default_factory=list)
    params: list[SignalLabParam] = Field(default_factory=list)
    reference_lines: list[SignalLabReferenceLine] = Field(default_factory=list)


class SignalLabIndexOption(BaseModel):
    """可选指数。"""

    index_code: str
    index_name: str


class SignalLabCatalog(BaseModel):
    """研究台目录：可研究的因子、信号、指数与边界说明。"""

    cross_section_factors: list[SignalLabDefinition]
    timing_signals: list[SignalLabDefinition]
    market_signals: list[SignalLabDefinition]
    indexes: list[SignalLabIndexOption]
    research_start: date
    research_end: date
    validation_start: date
    default_proxy: str
    max_range_days: int
    offline_notes: list[str] = Field(default_factory=list)


class SignalLabIssue(BaseModel):
    """数据问题 / 口径提示条目。"""

    level: str = "warn"
    code: str
    message: str


class SignalLabCoverage(BaseModel):
    """单指数在请求区间内的字段覆盖。"""

    index_code: str
    index_name: str = ""
    first_date: date | None = None
    last_date: date | None = None
    rows: int = 0
    close: float = 0.0
    ohl: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    used: bool = True
    reason: str | None = None


class SignalLabPerformance(BaseModel):
    """收益统计。"""

    total: float | None = None
    annualized: float | None = None
    max_drawdown: float | None = None
    sharpe: float | None = None


class SignalLabTimingMetrics(BaseModel):
    """择时信号评估指标。"""

    flips_per_year: float | None = None
    on_ratio: float | None = None
    ic_fwd20: float | None = None
    ic_past20: float | None = None
    corr_existing: float | None = None
    agree_existing: float | None = None
    strategy: SignalLabPerformance
    benchmark: SignalLabPerformance


class SignalLabYearStat(BaseModel):
    """择时分年统计。"""

    year: int
    strategy_return: float | None = None
    benchmark_return: float | None = None
    on_ratio: float | None = None


class SignalLabComponent(BaseModel):
    """信号中间分量序列。

    axis: price=与收盘价同轴；signal=与信号值同轴；aux=独立辅助图。
    """

    key: str
    label: str
    axis: str
    values: list[float | None]


class SignalLabTimingResponse(BaseModel):
    """单指数（或市场级）择时信号研究结果，序列按列存放。"""

    signal: SignalLabDefinition
    params: dict[str, float]
    index_code: str
    index_name: str = ""
    start: date
    end: date
    includes_validation: bool = False
    dates: list[str]
    close: list[float | None]
    value: list[float | None]
    on: list[float | None]
    components: list[SignalLabComponent] = Field(default_factory=list)
    strategy_nav: list[float | None] = Field(default_factory=list)
    benchmark_nav: list[float | None] = Field(default_factory=list)
    metrics: SignalLabTimingMetrics
    yearly: list[SignalLabYearStat] = Field(default_factory=list)
    coverage: SignalLabCoverage
    issues: list[SignalLabIssue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SignalLabICSummary(BaseModel):
    """截面 Rank IC 汇总。"""

    mean: float | None = None
    std: float | None = None
    icir: float | None = None
    positive_ratio: float | None = None
    n_periods: int = 0
    avg_assets: float | None = None


class SignalLabYearIC(BaseModel):
    """分年 IC。"""

    year: int
    ic_mean: float | None = None
    n_periods: int = 0


class SignalLabTercile(BaseModel):
    """三分组平均未来收益。"""

    group: str
    mean_return: float | None = None
    annualized: float | None = None


class SignalLabCorrelation(BaseModel):
    """与现有评分因子的平均截面相关。"""

    label: str
    corr: float | None = None


class SignalLabLatestItem(BaseModel):
    """最近调仓日的截面取值。"""

    index_code: str
    index_name: str = ""
    value: float | None = None
    rank: int | None = None
    forward_return: float | None = None


class SignalLabCrossSectionResponse(BaseModel):
    """截面因子 IC 研究结果。"""

    factor: SignalLabDefinition
    params: dict[str, float]
    horizon: int
    start: date
    end: date
    includes_validation: bool = False
    dates: list[str]
    ic: list[float | None]
    cum_ic: list[float | None]
    n_assets: list[int]
    summary: SignalLabICSummary
    yearly: list[SignalLabYearIC] = Field(default_factory=list)
    terciles: list[SignalLabTercile] = Field(default_factory=list)
    correlations: list[SignalLabCorrelation] = Field(default_factory=list)
    latest_date: str | None = None
    latest: list[SignalLabLatestItem] = Field(default_factory=list)
    coverage: list[SignalLabCoverage] = Field(default_factory=list)
    issues: list[SignalLabIssue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
