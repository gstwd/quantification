"""后台任务队列可观测性 schemas（B3）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from quant_etf_api.schemas.types import UtcDatetime


class QueueRunningJob(BaseModel):
    """运行中的队列任务摘要。

    Attributes:
        job_id: 任务 ID。
        job_type: 任务类型。
        lane: 所属 lane（backtest / general）。
        batch_id: 批次标识。
        attempts: 已尝试次数。
        elapsed_seconds: 已耗时（秒）。
        cancel_requested: 是否已请求取消。
    """

    job_id: str
    job_type: str
    lane: str
    batch_id: str | None = None
    attempts: int = 0
    elapsed_seconds: float = 0.0
    cancel_requested: bool = False


class QueueBacklogItem(BaseModel):
    """按任务类型统计的积压情况。

    Attributes:
        job_type: 任务类型。
        pending: 待执行数量。
        running: 执行中数量。
        paused: 暂停数量。
        oldest_pending_at: 最早待执行任务的创建时间。
    """

    job_type: str
    pending: int = 0
    running: int = 0
    paused: int = 0
    oldest_pending_at: UtcDatetime | None = None


class QueueCapacity(BaseModel):
    """当前 worker 并发预算与异常回收阈值（B1/B6/B7）。

    Attributes:
        general_workers: 通用 lane worker 线程数。
        backtest_workers: 回测 lane worker 线程数（回测并发预算）。
        heartbeat_interval_seconds: 心跳写库间隔。
        stuck_timeout_seconds: 心跳超时阈值。
        max_runtime_seconds: 单任务最长运行时间（0 表示不限制）。
        zombie_scan_enabled: 是否启用僵尸任务扫描。
    """

    general_workers: int
    backtest_workers: int
    heartbeat_interval_seconds: float
    stuck_timeout_seconds: float
    max_runtime_seconds: float
    zombie_scan_enabled: bool


class QueueStatsResponse(BaseModel):
    """队列统计响应。

    Attributes:
        status_counts: 各状态任务数量。
        pending: 待执行数量。
        running: 执行中数量。
        paused: 暂停数量。
        throughput_window_hours: 吞吐统计窗口（小时）。
        throughput: 窗口内完成数量（success/failed/cancelled）。
        backlog_by_type: 按类型统计的积压。
        running_jobs: 运行中任务列表。
        capacity: 并发预算与异常回收阈值。
    """

    status_counts: dict[str, int] = Field(default_factory=dict)
    pending: int = 0
    running: int = 0
    paused: int = 0
    throughput_window_hours: float = 1.0
    throughput: dict[str, int] = Field(default_factory=dict)
    backlog_by_type: list[QueueBacklogItem] = Field(default_factory=list)
    running_jobs: list[QueueRunningJob] = Field(default_factory=list)
    capacity: QueueCapacity


class QueueJobItem(BaseModel):
    """队列任务明细（用于人工排查"卡住了还是在跑"）。

    Attributes:
        job_id: 任务 ID。
        job_type: 任务类型。
        lane: 所属 lane。
        batch_id: 批次标识。
        status: 任务状态。
        priority: 优先级。
        attempts: 已尝试次数。
        max_attempts: 最大尝试次数。
        error_message: 失败/取消原因。
        cancel_requested: 是否已请求取消。
        created_at: 创建时间（UTC）。
        started_at: 开始执行时间（UTC）。
        heartbeat_at: 最近心跳时间（UTC）。
        finished_at: 完成时间（UTC）。
        queued_seconds: 待执行任务的等待时长（秒）。
        elapsed_seconds: 执行中任务已耗时（秒）。
    """

    job_id: str
    job_type: str
    lane: str
    batch_id: str | None = None
    status: str
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 1
    error_message: str | None = None
    cancel_requested: bool = False
    created_at: UtcDatetime | None = None
    started_at: UtcDatetime | None = None
    heartbeat_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    queued_seconds: float | None = None
    elapsed_seconds: float | None = None


class QueueJobsResponse(BaseModel):
    """队列任务明细列表响应。

    Attributes:
        items: 任务明细列表。
        total: 返回条数。
    """

    items: list[QueueJobItem] = Field(default_factory=list)
    total: int = 0
