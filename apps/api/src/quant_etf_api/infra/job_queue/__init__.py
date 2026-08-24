"""持久化后台任务队列包。

所有后台任务统一通过 background_job 表入队，由固定 worker 线程池
认领执行，替代原先分散的 executor / 裸线程 / 调度器内联执行路径。
"""

from quant_etf_api.infra.job_queue.queue import JobQueue, get_job_queue

__all__ = ["JobQueue", "get_job_queue"]
