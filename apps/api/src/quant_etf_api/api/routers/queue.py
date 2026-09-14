"""后台任务队列可观测性路由（B3）。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from quant_etf_api.infra.job_queue.queue import get_job_queue, lane_for
from quant_etf_api.infra.time import utcnow_aware
from quant_etf_api.schemas.queue import (
    QueueBacklogItem,
    QueueCapacity,
    QueueJobItem,
    QueueJobsResponse,
    QueueRunningJob,
    QueueStatsResponse,
)

router = APIRouter(tags=["queue"])


@router.get("/queue/stats", response_model=QueueStatsResponse)
def get_queue_stats(
    window_hours: float = Query(default=1.0, gt=0, le=168, description="吞吐统计窗口（小时）"),
) -> QueueStatsResponse:
    """返回队列积压、吞吐、运行中任务与并发预算。

    用于替代"手工连库查 pg_stat_activity / background_job"来判断
    "卡住了还是在跑"（B3）。
    """
    stats = get_job_queue().stats(window_hours=window_hours)
    return QueueStatsResponse(
        status_counts=stats["status_counts"],
        pending=stats["pending"],
        running=stats["running"],
        paused=stats["paused"],
        throughput_window_hours=stats["throughput_window_hours"],
        throughput=stats["throughput"],
        backlog_by_type=[QueueBacklogItem(**item) for item in stats["backlog_by_type"]],
        running_jobs=[QueueRunningJob(**item) for item in stats["running_jobs"]],
        capacity=QueueCapacity(**stats["capacity"]),
    )


@router.get("/queue/jobs", response_model=QueueJobsResponse)
def list_queue_jobs(
    status: Literal["pending", "running", "success", "failed", "cancelled", "paused"]
    | None = Query(default=None, description="任务状态过滤"),
    job_type: str | None = Query(default=None, description="任务类型过滤"),
    limit: int = Query(default=50, ge=1, le=500, description="返回条数上限"),
) -> QueueJobsResponse:
    """按状态/类型返回最近的后台任务明细。

    附带待执行任务的等待时长与执行中任务的已耗时，便于定位积压来源。
    """
    rows = get_job_queue().list_jobs(
        statuses=[status] if status else None,
        job_types=[job_type] if job_type else None,
        limit=limit,
    )
    now = utcnow_aware()
    items: list[QueueJobItem] = []
    for row in rows:
        queued_seconds = None
        if row.status == "pending" and row.created_at is not None:
            queued_seconds = round(max(0.0, (now - row.created_at).total_seconds()), 1)
        elapsed_seconds = None
        if row.status == "running" and row.started_at is not None:
            elapsed_seconds = round(max(0.0, (now - row.started_at).total_seconds()), 1)
        items.append(
            QueueJobItem(
                job_id=row.job_id,
                job_type=row.job_type,
                lane=lane_for(row.job_type),
                batch_id=row.batch_id,
                status=row.status,
                priority=row.priority,
                attempts=row.attempts,
                max_attempts=row.max_attempts,
                error_message=row.error_message,
                cancel_requested=row.cancel_requested,
                created_at=row.created_at,
                started_at=row.started_at,
                heartbeat_at=row.heartbeat_at,
                finished_at=row.finished_at,
                queued_seconds=queued_seconds,
                elapsed_seconds=elapsed_seconds,
            )
        )
    return QueueJobsResponse(items=items, total=len(items))
