"""策略生命周期监控的请求与响应模型。

生命周期模块只覆盖"人工标记上线之后"的阶段：上线时冻结配置快照与研究期
分布，之后每次刷新把上线后的表现填回该分布，输出分位、期望差与诊断结论。
状态（LIVE/SUSPENDED/RETIRED）只由人工变更，健康等级只由系统计算。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from quant_etf_api.schemas.types import UtcDatetime


class LifecycleOnlineRequest(BaseModel):
    """标记策略上线的请求体。

    Attributes:
        live_at: 上线业务日期（监控区间起点），留空时取当前北京时间日期。
        note: 上线备注（研究结论、上线依据），便于日后回溯决策背景。
        cost_bps: 净口径指标使用的单边成本（基点），留空取系统默认值。
    """

    live_at: date | None = None
    note: str | None = None
    cost_bps: float | None = None


class LifecycleStatusRequest(BaseModel):
    """变更生命周期状态的请求体（仅人工触发）。

    Attributes:
        status: 目标状态，LIVE=运行中，SUSPENDED=暂停观察，RETIRED=退役。
        note: 变更说明，写入上线记录备注。
    """

    status: Literal["LIVE", "SUSPENDED", "RETIRED"]
    note: str | None = None


class LifecycleRefreshRequest(BaseModel):
    """刷新健康快照的请求体。

    Attributes:
        cost_bps: 本次刷新使用的单边成本（基点），留空取系统默认值。
    """

    cost_bps: float | None = None


class HealthSnapshotSchema(BaseModel):
    """单次健康体检快照。

    Attributes:
        id: 快照自增主键。
        as_of_date: 快照对应的业务日期（监控区间截止日）。
        live_start: 监控区间起始日。
        live_end: 监控区间截止日。
        health_level: 健康等级（HEALTHY/WATCH/WARNING/CRITICAL）。
        diagnosis: 诊断结论。
        recommended_action: 建议动作。
        reasons: 判定依据（中文逐条说明）。
        trigger: 触发方式（manual/api）。
        computed_at: 计算时间（UTC）。
        metrics: 体检指标明细（滚动收益、分位、IC、净口径等）。
    """

    id: int
    as_of_date: date
    live_start: date
    live_end: date
    health_level: str
    diagnosis: str
    recommended_action: str
    reasons: list[str] = Field(default_factory=list)
    trigger: str = "manual"
    computed_at: UtcDatetime | None = None
    metrics: dict[str, Any] | None = None


class LifecycleSummary(BaseModel):
    """生命周期列表项。

    Attributes:
        strategy_id: 策略 ID。
        display_name: 策略中文名，策略被删除时为 None。
        lifecycle_status: 生命周期状态。
        live_at: 上线日期。
        retired_at: 退役日期。
        live_days: 上线至今天数（自然日）。
        health_level: 最近一次刷新的健康等级。
        diagnosis: 最近一次刷新的诊断结论。
        recommended_action: 最近一次刷新的建议动作。
        last_refreshed_at: 最近一次刷新时间（UTC）。
    """

    strategy_id: str
    display_name: str | None = None
    lifecycle_status: str
    live_at: date
    retired_at: date | None = None
    live_days: int = 0
    health_level: str | None = None
    diagnosis: str | None = None
    recommended_action: str | None = None
    last_refreshed_at: UtcDatetime | None = None


class LifecycleDetail(LifecycleSummary):
    """生命周期详情，含冻结快照信息与最近的体检记录。

    Attributes:
        frozen_config_hash: 上线时冻结的配置哈希。
        frozen_config_snapshot: 上线时冻结的配置快照。
        research_backtest_id: 研究期基线回测 ID。
        validation_backtest_id: 最近一次验证期回测 ID。
        baseline_distribution: 研究期分布快照。
        note: 上线备注。
        created_at: 上线记录创建时间（UTC）。
        updated_at: 记录最后更新时间（UTC）。
        snapshots: 最近的体检快照（按时间倒序）。
    """

    frozen_config_hash: str = ""
    frozen_config_snapshot: dict[str, Any] | None = None
    research_backtest_id: str | None = None
    validation_backtest_id: str | None = None
    baseline_distribution: dict[str, Any] | None = None
    note: str | None = None
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    snapshots: list[HealthSnapshotSchema] = Field(default_factory=list)
