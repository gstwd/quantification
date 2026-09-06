"""申万行业轮动子系统 API 模型（调试页与只读研究端点）。"""

from __future__ import annotations

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
