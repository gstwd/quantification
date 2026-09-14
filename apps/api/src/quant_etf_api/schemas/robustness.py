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
        deleted_windows: 已被清理的窗口标签（其回测已被删除，C5）。
    """

    label: str
    kind: str
    knob: str | None = None
    value: Any = None
    strategy_id: str | None = None
    backtest_ids: dict[str, str] = Field(default_factory=dict)
    deleted_windows: list[str] = Field(default_factory=list)


class RobustnessSummary(BaseModel):
    """稳健性验证批次摘要。

    Attributes:
        robustness_id: 批次唯一 ID。
        strategy_id: 基线策略 ID。
        strategy_version: 基线策略版本。
        kind: 验证类型（scan/ablate/pool）。
        status: 批次状态（running/success/failed/partial/cancelled）。
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
        missing_backtest_ids: 引用但已不存在的回测 ID（C5 悬挂引用提示）。
    """

    baseline_config_hash: str = ""
    windows: list[dict[str, Any]] = Field(default_factory=list)
    variants: list[RobustnessVariantSchema] = Field(default_factory=list)
    summary: dict[str, Any] | None = None
    statistics: dict[str, Any] | None = None
    missing_backtest_ids: list[str] = Field(default_factory=list)


class RobustnessListResponse(BaseModel):
    """稳健性验证批次列表响应。

    Attributes:
        items: 批次摘要列表（按创建时间倒序）。
        total: 记录总数。
    """

    items: list[RobustnessSummary] = Field(default_factory=list)
    total: int = 0


class RobustnessControlResponse(BaseModel):
    """批次控制（取消 / 暂停 / 恢复）结果（B2）。

    Attributes:
        robustness_id: 批次 ID。
        status: 操作后的批次状态（取消操作返回）。
        cancelled_jobs: 直接取消的队列任务数。
        cancel_requested_jobs: 打协作取消标记的运行中任务数。
        cancelled_backtests: 同步执行、由服务端直接落为 cancelled 的回测数。
        paused_jobs: 被暂停的队列任务数。
        resumed_jobs: 被恢复的队列任务数。
    """

    robustness_id: str
    status: str | None = None
    cancelled_jobs: int = 0
    cancel_requested_jobs: int = 0
    cancelled_backtests: int = 0
    paused_jobs: int = 0
    resumed_jobs: int = 0
