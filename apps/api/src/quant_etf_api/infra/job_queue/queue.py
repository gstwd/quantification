"""后台任务队列：持久化入队、worker 认领与执行。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.db.models.core import BackgroundJobModel
from quant_etf_api.infra.job_queue.repository import ClaimedJob, JobRepository

logger = logging.getLogger(__name__)


class JobQueue:
    """基于 PostgreSQL 的持久化后台任务队列。

    所有后台任务通过 enqueue 写入 background_job 表，
    由固定数量的 worker 线程以 FOR UPDATE SKIP LOCKED 认领执行。
    支持按 job_key 幂等去重，以及失败后按 max_attempts 重试。
    """

    def __init__(
        self,
        repo: JobRepository | None = None,
        workers: int | None = None,
        poll_interval: float | None = None,
    ) -> None:
        """初始化任务队列。

        Args:
            repo: 任务仓库，未提供时自动创建。
            workers: worker 线程数，未提供时使用 settings.job_queue_workers。
            poll_interval: 队列空转时的轮询间隔（秒），未提供时使用设置值。
        """
        settings = get_settings()
        self._repo = repo or JobRepository()
        self._workers = workers if workers is not None else settings.job_queue_workers
        self._poll_interval = (
            poll_interval if poll_interval is not None else settings.job_poll_interval_seconds
        )
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def enqueue(
        self,
        job_type: str,
        payload: dict | None = None,
        job_key: str | None = None,
        priority: int = 0,
        max_attempts: int = 1,
    ) -> str:
        """入队一个后台任务，相同 job_key 未完成时幂等跳过。

        Args:
            job_type: 任务类型，对应 JOB_HANDLERS 中的处理器。
            payload: 任务参数。
            job_key: 去重键，pending/running 状态唯一。
            priority: 优先级，越大越先执行。
            max_attempts: 最大尝试次数。

        Returns:
            任务 ID（已存在时返回既有任务 ID）。
        """
        if job_key:
            existing = self._repo.find_active_by_key(job_key)
            if existing is not None:
                return existing

        job_id = str(uuid4())
        job = BackgroundJobModel(
            job_id=job_id,
            job_type=job_type,
            job_key=job_key,
            payload=payload,
            status="pending",
            priority=priority,
            attempts=0,
            max_attempts=max_attempts,
        )
        try:
            self._repo.create(job)
        except IntegrityError:
            # 并发入队同一 job_key 时依赖部分唯一索引兜底去重
            if job_key:
                existing = self._repo.find_active_by_key(job_key)
                if existing is not None:
                    return existing
            raise
        return job_id

    def start(self) -> None:
        """启动固定数量的 worker 线程。"""
        if self._threads:
            return
        self._stop_event.clear()
        for i in range(self._workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"job-worker-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        logger.info("任务队列 worker 已启动: workers=%d", self._workers)

    def stop(self) -> None:
        """停止 worker 线程，最多等待当前任务完成。"""
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=30)
        self._threads = []
        logger.info("任务队列 worker 已停止")

    def recover_stuck_jobs(self) -> int:
        """恢复进程重启后卡在 running 状态的任务。

        Returns:
            恢复的任务数量。
        """
        return self._repo.recover_stuck_jobs()

    def _worker_loop(self) -> None:
        """worker 主循环：认领一个任务并执行，空队列时按间隔轮询。"""
        while not self._stop_event.is_set():
            try:
                claimed = self._repo.claim_pending(1)
            except Exception:
                logger.exception("任务认领失败，等待后重试")
                self._stop_event.wait(self._poll_interval)
                continue
            if claimed:
                self._execute(claimed[0])
                continue
            self._stop_event.wait(self._poll_interval)

    def _execute(self, job: ClaimedJob) -> None:
        """执行单个任务并更新状态，失败时按 max_attempts 决定重试或失败。

        Args:
            job: 已认领的任务快照。
        """
        try:
            handler = self._get_handler(job.job_type)
            handler(job.payload or {})
            self._repo.mark_success(job.job_id)
        except Exception as exc:
            logger.exception(
                "后台任务执行失败: job_id=%s job_type=%s", job.job_id, job.job_type
            )
            if (job.attempts or 0) >= (job.max_attempts or 1):
                self._repo.mark_failed(job.job_id, f"{type(exc).__name__}: {exc}")
            else:
                self._repo.reset_to_pending(job.job_id)

    @staticmethod
    def _get_handler(job_type: str) -> Callable[[dict], None]:
        """按任务类型获取处理器，未知类型抛出 ValueError。

        Args:
            job_type: 任务类型。

        Returns:
            对应的处理器函数。
        """
        from quant_etf_api.infra.job_queue.handlers import JOB_HANDLERS

        handler = JOB_HANDLERS.get(job_type)
        if handler is None:
            raise ValueError(f"未知任务类型: {job_type}")
        return handler


_job_queue: JobQueue | None = None


def get_job_queue() -> JobQueue:
    """获取全局任务队列单例。"""
    global _job_queue
    if _job_queue is None:
        _job_queue = JobQueue()
    return _job_queue
