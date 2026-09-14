"""持久化后台任务队列包。

所有后台任务统一通过 background_job 表入队，由按 lane 划分的 worker
线程池认领执行，替代原先分散的 executor / 裸线程 / 调度器内联执行路径。
回测类任务独立成 lane 并持有独立并发预算（B1/B6）。
"""

from quant_etf_api.infra.job_queue.context import JobCancelledError, ensure_not_cancelled
from quant_etf_api.infra.job_queue.queue import (
    BACKTEST_LANE_JOB_TYPES,
    LANE_BACKTEST,
    LANE_GENERAL,
    JobQueue,
    backtest_job_key,
    get_job_queue,
    lane_for,
    reset_job_queue,
)

__all__ = [
    "BACKTEST_LANE_JOB_TYPES",
    "LANE_BACKTEST",
    "LANE_GENERAL",
    "JobCancelledError",
    "JobQueue",
    "backtest_job_key",
    "ensure_not_cancelled",
    "get_job_queue",
    "lane_for",
    "reset_job_queue",
]
