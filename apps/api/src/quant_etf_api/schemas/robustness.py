"""稳健性验证（robustness）批次的响应模型。

稳健性验证在候选集级别回答"挑出来的赢家有多少是运气"：单次回测无法计算
PBO、Deflated Sharpe 等指标，因为它们依赖候选集合与试验次数。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from quant_etf_api.schemas.types import UtcDatetime


class RobustnessVariantSchema(BaseModel):
    """单个扰动变体。

    Attributes:
        label: 变体标签（如 topn_02、ablate_return_20d、rand80_03）。
        kind: 变体类型（knob/ablation/pool）。
        knob: 被扰动的配置路径，池扰动为 index_codes。
        value: 扰动后的取值，池扰动为资产代码列表。
        strategy_id: 该变体对应的草稿策略 ID。
        backtest_ids: 窗口标签 → 回测 ID 的映射。
    """

    label: str
    kind: str
    knob: str | None = None
    value: Any = None
    strategy_id: str | None = None
    backtest_ids: dict[str, str] = Field(default_factory=dict)


class RobustnessSummary(BaseModel):
    """稳健性验证批次摘要。

    Attributes:
        robustness_id: 批次唯一 ID。
        strategy_id: 基线策略 ID。
        strategy_version: 基线策略版本。
        kind: 验证类型（scan/ablate/pool）。
        status: 批次状态（running/success/failed）。
        start_date: 验证区间起始日期。
        end_date: 验证区间截止日期。
        trial_count: 批次内独立变体数量（试验次数台账口径）。
        created_at: 创建时间（UTC）。
        finished_at: 完成时间（UTC）。
        error_message: 失败信息。
    """

    robustness_id: str
    strategy_id: str
    strategy_version: str = ""
    kind: str
    status: str
    start_date: date
    end_date: date
    trial_count: int = 0
    created_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error_message: str | None = None


class RobustnessDetail(RobustnessSummary):
    """稳健性验证详情。

    Attributes:
        baseline_config_hash: 基线配置哈希。
        windows: 评估窗口列表，元素为 {label, start, end}。
        variants: 变体列表。
        summary: 邻域稳定度 / 边际贡献 / 池扰动汇总。
        statistics: PBO、Deflated Sharpe、块自助法置信区间。
    """

    baseline_config_hash: str = ""
    windows: list[dict[str, Any]] = Field(default_factory=list)
    variants: list[RobustnessVariantSchema] = Field(default_factory=list)
    summary: dict[str, Any] | None = None
    statistics: dict[str, Any] | None = None


class RobustnessListResponse(BaseModel):
    """稳健性验证批次列表响应。

    Attributes:
        items: 批次摘要列表（按创建时间倒序）。
        total: 记录总数。
    """

    items: list[RobustnessSummary] = Field(default_factory=list)
    total: int = 0
