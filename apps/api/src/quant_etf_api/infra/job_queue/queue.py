"""后台任务队列：持久化入队、按 lane 划分的 worker 认领与执行。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.db.models.core import BackgroundJobModel
from quant_etf_api.infra.job_queue.context import (
    JobCancelledError,
    clear_cache,
    reset_current_job,
    set_current_job,
)
from quant_etf_api.infra.job_queue.repository import ClaimedJob, JobRepository
from quant_etf_api.infra.time import utcnow_aware

logger = logging.getLogger(__name__)

# lane 名称：回测 lane 与通用 lane 各自持有独立 worker 配额（B1/B6）。
# 回测是 CPU + 数据库混合型任务，实测单进程内并发 4 条比串行慢约 20 倍；
# 把它隔离出来既能约束回测的数据库并发预算，也不会让长批次占满通用 worker
# 而拖住数据摄取与因子计算。
LANE_BACKTEST = "backtest"
LANE_GENERAL = "general"

# 回测 lane 承载的任务类型；其余任务类型统一归入通用 lane
BACKTEST_LANE_JOB_TYPES: tuple[str, ...] = ("backtest", "comparison")


def lane_for(job_type: str) -> str:
    """返回任务类型所属的 lane。

    Args:
        job_type: 任务类型。

    Returns:
        lane 名称（``backtest`` 或 ``general``）。
    """
    return LANE_BACKTEST if job_type in BACKTEST_LANE_JOB_TYPES else LANE_GENERAL


def backtest_job_key(backtest_id: str) -> str:
    """回测任务的统一去重键。

    除幂等去重外，回测详情的排队位置/等待耗时也依赖该键反查
    background_job 记录（B3），因此所有入队点必须使用同一命名。

    Args:
        backtest_id: 回测标识。

    Returns:
        任务去重键。
    """
    return f"backtest:{backtest_id}"


class JobQueue:
    """基于 PostgreSQL 的持久化后台任务队列。

    所有后台任务通过 enqueue 写入 background_job 表，由按 lane 划分的
    worker 线程以 FOR UPDATE SKIP LOCKED 认领执行。
    支持按 job_key 幂等去重、按 max_attempts 重试、按 priority 抢跑，
    以及批次级取消/暂停（B2）与心跳超时回收（B7）。
    """

    def __init__(
        self,
        repo: JobRepository | None = None,
        workers: int | None = None,
        poll_interval: float | None = None,
        backtest_workers: int | None = None,
        heartbeat_interval: float | None = None,
        stuck_timeout: float | None = None,
        zombie_scan_enabled: bool | None = None,
        zombie_scan_interval: float | None = None,
        max_runtime: float | None = None,
    ) -> None:
        """初始化任务队列。

        Args:
            repo: 任务仓库，未提供时自动创建。
            workers: 通用 lane 的 worker 线程数，未提供时使用设置值。
            poll_interval: 队列空转时的轮询间隔（秒），未提供时使用设置值。
            backtest_workers: 回测 lane 的 worker 线程数（并发预算）。
            heartbeat_interval: 心跳写库间隔（秒）。
            stuck_timeout: 心跳超时阈值（秒）。
            zombie_scan_enabled: 是否启用运行期僵尸任务扫描线程。
            zombie_scan_interval: 僵尸任务扫描间隔（秒）。
            max_runtime: 单任务最长运行时间（秒），0 表示不限制。
        """
        settings = get_settings()
        self._repo = repo or JobRepository()
        self._workers = workers if workers is not None else settings.job_queue_workers
        self._backtest_workers = (
            backtest_workers
            if backtest_workers is not None
            else settings.job_queue_backtest_workers
        )
        self._poll_interval = (
            poll_interval if poll_interval is not None else settings.job_poll_interval_seconds
        )
        self._heartbeat_interval = (
            heartbeat_interval
            if heartbeat_interval is not None
            else settings.job_heartbeat_interval_seconds
        )
        self._stuck_timeout = (
            stuck_timeout if stuck_timeout is not None else settings.job_stuck_timeout_seconds
        )
        self._zombie_scan_enabled = (
            zombie_scan_enabled
            if zombie_scan_enabled is not None
            else settings.job_zombie_scan_enabled
        )
        self._zombie_scan_interval = (
            zombie_scan_interval
            if zombie_scan_interval is not None
            else settings.job_zombie_scan_interval_seconds
        )
        self._max_runtime = (
            max_runtime if max_runtime is not None else settings.job_max_runtime_seconds
        )
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._scanner_thread: threading.Thread | None = None
        self._wake_event = threading.Event()

    # ── 入队与查询 ────────────────────────────────────────────────────────

    def enqueue(
        self,
        job_type: str,
        payload: dict | None = None,
        job_key: str | None = None,
        priority: int = 0,
        max_attempts: int = 1,
        batch_id: str | None = None,
    ) -> str:
        """入队一个后台任务，相同 job_key 未完成时幂等跳过。

        Args:
            job_type: 任务类型，对应 JOB_HANDLERS 中的处理器。
            payload: 任务参数。
            job_key: 去重键，pending/running/paused 状态唯一。
            priority: 优先级，越大越先执行。
            max_attempts: 最大尝试次数。
            batch_id: 批次标识（如稳健性批次 ID），支持整批取消/暂停。

        Returns:
            任务 ID（已存在时返回既有任务 ID）。
        """
        job_id, _ = self.enqueue_with_status(
            job_type, payload, job_key, priority, max_attempts, batch_id
        )
        return job_id

    def enqueue_with_status(
        self,
        job_type: str,
        payload: dict | None = None,
        job_key: str | None = None,
        priority: int = 0,
        max_attempts: int = 1,
        batch_id: str | None = None,
    ) -> tuple[str, bool]:
        """入队并返回任务是否由本次请求新建。

        调用方在创建 research_run 后可据此把未实际入队的运行标记为跳过，
        防止任务去重后遗留永远 pending 的运行记录。

        Args:
            job_type: 任务类型。
            payload: 任务参数。
            job_key: 去重键。
            priority: 优先级。
            max_attempts: 最大尝试次数。
            batch_id: 批次标识。

        Returns:
            (job_id, 是否新建)。
        """
        if job_key:
            existing = self._repo.find_active_by_key(job_key)
            if existing is not None:
                return existing, False

        job_id = str(uuid4())
        job = BackgroundJobModel(
            job_id=job_id,
            job_type=job_type,
            job_key=job_key,
            batch_id=batch_id,
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
                    return existing, False
            raise
        self._wake_event.set()
        return job_id, True

    def find_active_payload(self, job_key: str) -> dict | None:
        """返回指定去重键当前运行任务的载荷副本。"""
        return self._repo.find_active_payload_by_key(job_key)

    def find_snapshots_by_keys(self, job_keys: Sequence[str]) -> dict[str, ClaimedJob]:
        """按去重键批量查询任务快照（供可观测性接口使用）。"""
        return self._repo.find_snapshots_by_keys(job_keys)

    def list_jobs(
        self,
        statuses: Sequence[str] | None = None,
        job_types: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[ClaimedJob]:
        """按状态/类型返回最近的任务快照（按创建时间倒序）。

        Args:
            statuses: 状态白名单。
            job_types: 类型白名单。
            limit: 返回条数上限。

        Returns:
            任务快照列表。
        """
        return self._repo.list_jobs(statuses=statuses, job_types=job_types, limit=limit)

    def completion_handler(self, job_type: str) -> Callable[[dict], None] | None:
        """返回任务类型的处理器（仅用于测试与诊断）。

        Args:
            job_type: 任务类型。

        Returns:
            处理器函数；未知类型返回 None。
        """
        from quant_etf_api.infra.job_queue.handlers import JOB_HANDLERS

        return JOB_HANDLERS.get(job_type)

    def queue_position(self, job: ClaimedJob) -> int:
        """返回任务在当前 lane 内的排队位置（0 表示下一个执行）。

        Args:
            job: 任务快照（含 created_at 与 priority）。

        Returns:
            排在该任务之前的待执行任务数量。
        """
        if job.created_at is None:
            return 0
        job_types = BACKTEST_LANE_JOB_TYPES if lane_for(job.job_type) == LANE_BACKTEST else None
        # 传 job_id 兜底：批量入队时 created_at 可能相同，只有带上同一兜底次序键，
        # 队列位置才与 worker 的实际认领次序一致（B3）
        return self._repo.count_pending_ahead(
            job.priority, job.created_at, job_types, job.job_id
        )

    # ── 取消 / 暂停 ──────────────────────────────────────────────────────

    def cancel_job(self, job_id: str, message: str = "任务已取消") -> str | None:
        """请求取消单个任务（未开始直接取消，运行中协作退出）。

        Args:
            job_id: 任务标识。
            message: 取消原因。

        Returns:
            处理后的任务状态；任务不存在时返回 None。
        """
        status = self._repo.request_cancel(job_id, message)
        clear_cache(job_id)
        return status

    def cancel_batch(self, batch_id: str, message: str = "批次已取消") -> dict[str, int]:
        """请求取消整批任务。

        Args:
            batch_id: 批次标识。
            message: 取消原因。

        Returns:
            {"cancelled": 直接取消数, "requested": 协作取消数}。
        """
        result = self._repo.cancel_batch(batch_id, message)
        self._wake_event.set()
        return result

    def pause_batch(self, batch_id: str, message: str = "批次已暂停") -> int:
        """暂停整批未开始的任务。

        Args:
            batch_id: 批次标识。
            message: 暂停说明。

        Returns:
            被暂停的任务数。
        """
        return self._repo.pause_batch(batch_id, message)

    def resume_batch(self, batch_id: str) -> int:
        """恢复整批被暂停的任务。

        Args:
            batch_id: 批次标识。

        Returns:
            被恢复的任务数。
        """
        count = self._repo.resume_batch(batch_id)
        self._wake_event.set()
        return count

    def is_cancel_requested(self, job_id: str) -> bool:
        """查询任务是否已被请求取消。"""
        return self._repo.is_cancel_requested(job_id)

    # ── 可观测性 ─────────────────────────────────────────────────────────

    def stats(self, window_hours: float = 1.0) -> dict[str, Any]:
        """汇总队列积压、吞吐与运行中任务（B3）。

        Args:
            window_hours: 吞吐统计窗口（小时）。

        Returns:
            队列统计字典。
        """
        counts = self._repo.status_counts()
        since = utcnow_aware() - timedelta(hours=max(0.01, window_hours))
        throughput = self._repo.throughput_since(since)
        now = utcnow_aware()
        running: list[dict[str, Any]] = []
        for row in self._repo.list_jobs(statuses=["running"], limit=50):
            elapsed = 0.0
            if row.started_at is not None:
                elapsed = max(0.0, (now - row.started_at).total_seconds())
            running.append(
                {
                    "job_id": row.job_id,
                    "job_type": row.job_type,
                    "lane": lane_for(row.job_type),
                    "batch_id": row.batch_id,
                    "attempts": row.attempts,
                    "elapsed_seconds": round(elapsed, 1),
                    "cancel_requested": row.cancel_requested,
                }
            )
        return {
            "status_counts": counts,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "paused": counts.get("paused", 0),
            "throughput_window_hours": window_hours,
            "throughput": throughput,
            "backlog_by_type": self._repo.type_backlog(),
            "running_jobs": running,
            "capacity": {
                "general_workers": self._workers,
                "backtest_workers": self._backtest_workers,
                "heartbeat_interval_seconds": self._heartbeat_interval,
                "stuck_timeout_seconds": self._stuck_timeout,
                "max_runtime_seconds": self._max_runtime,
                "zombie_scan_enabled": self._zombie_scan_enabled,
            },
        }

    # ── 生命周期 ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动各 lane 的 worker 线程与僵尸任务扫描线程。"""
        if self._threads:
            return
        self._stop_event.clear()
        for i in range(self._workers):
            self._start_worker(LANE_GENERAL, i)
        for i in range(self._backtest_workers):
            self._start_worker(LANE_BACKTEST, i)
        if self._zombie_scan_enabled:
            self._scanner_thread = threading.Thread(
                target=self._zombie_scan_loop,
                name="job-zombie-scanner",
                daemon=True,
            )
            self._scanner_thread.start()
        logger.info(
            "任务队列 worker 已启动: general=%d backtest=%d 僵尸扫描=%s 单任务上限=%ss",
            self._workers,
            self._backtest_workers,
            self._zombie_scan_enabled,
            self._max_runtime,
        )

    def stop(self) -> None:
        """停止 worker 线程，最多等待当前任务完成。"""
        self._stop_event.set()
        self._wake_event.set()
        for t in self._threads:
            t.join(timeout=30)
        self._threads = []
        if self._scanner_thread is not None:
            self._scanner_thread.join(timeout=5)
            self._scanner_thread = None
        logger.info("任务队列 worker 已停止")

    def recover_stuck_jobs(self) -> int:
        """恢复冷启动前遗留的卡死任务（仅限心跳超时者）。

        判据与 :meth:`scan_zombies` 一致：只有心跳超过 ``stuck_timeout``
        未更新的 running 任务才被回收。因此在系统运行期启动本 worker
        进程（或重启 API 进程）不会影响其它进程正在正常心跳的任务，
        避免误判失败后 ``job_key`` 去重失效、同一任务被并发执行两份。

        Returns:
            恢复的任务数量。
        """
        return self._repo.recover_stuck_jobs(self._stuck_timeout)

    def scan_zombies(self) -> int:
        """回收心跳超时（或超长运行）的 running 任务。

        心跳超时说明执行进程已死；即使心跳仍在更新，只要超过 max_runtime
        也判定为异常长任务并回收，避免单条任务永久占用 worker 并让整批
        稳健性回测无法收口（B7）。

        Returns:
            本轮回收的任务数量。
        """
        recovered = 0
        try:
            stale = self._repo.find_stale_running(self._stuck_timeout)
        except Exception:
            logger.warning("僵尸任务扫描失败", exc_info=True)
            return 0
        for job in stale:
            self._abandon_job(job, f"心跳超时（超过 {self._stuck_timeout:.0f} 秒未更新）")
            recovered += 1
        if self._max_runtime and self._max_runtime > 0:
            try:
                overrun = self._repo.find_running_over(self._max_runtime)
            except Exception:
                logger.warning("超长任务扫描失败", exc_info=True)
                overrun = []
            for job in overrun:
                self._abandon_job(job, f"单任务运行超过 {self._max_runtime:.0f} 秒上限")
                recovered += 1
        return recovered

    def _abandon_job(self, job: ClaimedJob, reason: str) -> None:
        """回收一个异常任务：通知业务侧清理，并按剩余尝试次数重试或判失败。"""
        logger.warning(
            "回收异常任务: job_id=%s job_type=%s 已尝试=%s/%s 原因=%s",
            job.job_id,
            job.job_type,
            job.attempts,
            job.max_attempts,
            reason,
        )
        _notify_job_abandoned(job, reason)
        if (job.attempts or 0) < (job.max_attempts or 1):
            self._repo.reset_to_pending(job.job_id)
        else:
            self._repo.mark_failed(job.job_id, f"任务异常终止：{reason}")

    def _start_worker(self, lane: str, index: int) -> None:
        """按 lane 启动单个 worker 线程。"""
        t = threading.Thread(
            target=self._worker_loop,
            args=(lane,),
            name=f"job-worker-{lane}-{index}",
            daemon=True,
        )
        t.start()
        self._threads.append(t)

    def _worker_loop(self, lane: str) -> None:
        """worker 主循环：认领本 lane 的一个任务并执行，空队列时按间隔轮询。"""
        while not self._stop_event.is_set():
            try:
                if lane == LANE_BACKTEST:
                    claimed = self._repo.claim_pending(1, job_types=BACKTEST_LANE_JOB_TYPES)
                else:
                    claimed = self._repo.claim_pending(1, exclude_job_types=BACKTEST_LANE_JOB_TYPES)
            except Exception:
                logger.exception("任务认领失败（lane=%s），等待后重试", lane)
                self._stop_event.wait(self._poll_interval)
                continue
            if claimed:
                self._execute(claimed[0])
                continue
            # 入队会 set 唤醒事件，避免空转轮询造成固定延迟
            self._wake_event.wait(self._poll_interval)
            self._wake_event.clear()

    def _zombie_scan_loop(self) -> None:
        """僵尸任务扫描线程：周期性回收异常任务（B7）。"""
        while not self._stop_event.wait(self._zombie_scan_interval):
            try:
                count = self.scan_zombies()
                if count:
                    logger.warning("僵尸任务扫描回收 %d 条任务", count)
            except Exception:
                logger.exception("僵尸任务扫描异常")

    def _execute(self, job: ClaimedJob) -> None:
        """执行单个任务并更新状态，失败时按 max_attempts 决定重试或失败。

        Args:
            job: 已认领的任务快照。
        """
        token = set_current_job(job.job_id)
        heartbeat = _HeartbeatWorker(job.job_id, self._repo, self._heartbeat_interval)
        heartbeat.start()
        try:
            if job.cancel_requested:
                raise JobCancelledError("任务在执行前已被请求取消")
            handler = self._get_handler(job.job_type)
            handler(job.payload or {})
            self._repo.mark_success(job.job_id)
        except JobCancelledError:
            logger.info("任务已取消: job_id=%s job_type=%s", job.job_id, job.job_type)
            self._repo.mark_cancelled(job.job_id, "任务已按请求取消")
        except Exception as exc:
            logger.exception("后台任务执行失败: job_id=%s job_type=%s", job.job_id, job.job_type)
            if (job.attempts or 0) >= (job.max_attempts or 1):
                self._repo.mark_failed(job.job_id, f"{type(exc).__name__}: {exc}")
            else:
                self._repo.reset_to_pending(job.job_id)
        finally:
            heartbeat.stop()
            clear_cache(job.job_id)
            reset_current_job(token)

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


class _HeartbeatWorker:
    """任务心跳线程：执行期间周期性更新 background_job.heartbeat_at。"""

    def __init__(self, job_id: str, repo: JobRepository, interval: float) -> None:
        """初始化心跳线程。

        Args:
            job_id: 任务标识。
            repo: 任务仓库。
            interval: 心跳间隔（秒）。
        """
        self._job_id = job_id
        self._repo = repo
        self._interval = max(1.0, interval)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"job-heartbeat-{job_id[:8]}", daemon=True
        )

    def start(self) -> None:
        """启动心跳线程。"""
        self._thread.start()

    def stop(self) -> None:
        """停止心跳线程。"""
        self._stop.set()
        self._thread.join(timeout=2)

    def _loop(self) -> None:
        """心跳循环：按间隔写库，直到任务结束。"""
        while not self._stop.wait(self._interval):
            try:
                self._repo.heartbeat(self._job_id)
            except Exception:
                logger.debug("任务心跳写入失败: job_id=%s", self._job_id, exc_info=True)


def _notify_job_abandoned(job: ClaimedJob, reason: str) -> None:
    """通知业务侧清理被回收的异常任务（尽力而为）。"""
    try:
        from quant_etf_api.infra.job_queue.handlers import JOB_ABANDON_HANDLERS

        callback = JOB_ABANDON_HANDLERS.get(job.job_type)
        if callback is not None:
            callback(job.payload or {}, reason)
    except Exception:
        logger.warning("异常任务业务侧清理失败: job_id=%s", job.job_id, exc_info=True)


_job_queue: JobQueue | None = None


def get_job_queue() -> JobQueue:
    """获取全局任务队列单例。"""
    global _job_queue
    if _job_queue is None:
        _job_queue = JobQueue()
    return _job_queue


def reset_job_queue() -> None:
    """重置全局队列单例（测试或调整并发预算后重建时使用）。"""
    global _job_queue
    _job_queue = None
