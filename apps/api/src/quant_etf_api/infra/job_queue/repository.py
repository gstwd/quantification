"""后台任务队列仓库。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.base import SessionLocal, utcnow
from quant_etf_api.infra.db.models.core import BackgroundJobModel


@dataclass
class ClaimedJob:
    """认领任务的行快照。

    从 ORM 实例复制字段，脱离 Session 后仍可安全读取，
    避免 commit 后实例过期导致的 DetachedInstanceError。
    """

    job_id: str
    job_type: str
    payload: dict | None
    status: str
    priority: int
    attempts: int
    max_attempts: int
    error_message: str | None


class JobRepository:
    """BackgroundJobModel 的查询与状态更新仓库。

    每个方法使用独立的短生命周期 Session，保证多个 worker 线程
    并发操作同一张表时不会共享同一个 Session。
    """

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        """初始化任务仓库。

        Args:
            session_factory: Session 工厂，默认使用全局 SessionLocal。
        """
        self._session_factory = session_factory

    def create(self, job: BackgroundJobModel) -> None:
        """写入新的任务记录并提交。

        Args:
            job: 待写入的任务对象。

        Raises:
            IntegrityError: job_key 违反部分唯一索引（并发去重冲突）。
        """
        db = self._session_factory()
        try:
            db.add(job)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def find_active_by_key(self, job_key: str) -> str | None:
        """按去重键查询未完成（pending/running）的任务。

        Args:
            job_key: 去重键。

        Returns:
            未完成任务的 job_id，不存在时返回 None。
        """
        db = self._session_factory()
        try:
            row = (
                db.query(BackgroundJobModel)
                .filter(
                    BackgroundJobModel.job_key == job_key,
                    BackgroundJobModel.status.in_(["pending", "running"]),
                )
                .first()
            )
            return row.job_id if row is not None else None
        finally:
            db.close()

    def claim_pending(self, limit: int = 1) -> list[ClaimedJob]:
        """原子认领待执行任务，置为 running 并累计尝试次数。

        使用 PostgreSQL 的 FOR UPDATE SKIP LOCKED 保证多 worker
        并发认领时不会重复执行同一任务。

        Args:
            limit: 单次认领的最大任务数。

        Returns:
            认领成功的任务快照列表（已脱离 Session，可安全读取属性）。
        """
        db = self._session_factory()
        try:
            rows = (
                db.query(BackgroundJobModel)
                .filter(BackgroundJobModel.status == "pending")
                .order_by(
                    BackgroundJobModel.priority.desc(),
                    BackgroundJobModel.created_at.asc(),
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
                .all()
            )
            now = utcnow()
            claimed: list[ClaimedJob] = []
            for job in rows:
                job.status = "running"
                job.attempts = (job.attempts or 0) + 1
                job.started_at = now
                # 在 Session 关闭前复制字段为快照，避免游离实例访问过期属性
                claimed.append(
                    ClaimedJob(
                        job_id=job.job_id,
                        job_type=job.job_type,
                        payload=job.payload,
                        status=job.status,
                        priority=job.priority or 0,
                        attempts=job.attempts,
                        max_attempts=job.max_attempts or 1,
                        error_message=job.error_message,
                    )
                )
            db.commit()
            return claimed
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def mark_success(self, job_id: str) -> None:
        """将任务标记为成功，记录完成时间。"""
        db = self._session_factory()
        try:
            job = db.get(BackgroundJobModel, job_id)
            if job is None:
                return
            job.status = "success"
            job.finished_at = utcnow()
            db.commit()
        finally:
            db.close()

    def mark_failed(self, job_id: str, error_message: str) -> None:
        """将任务标记为失败，记录错误信息。"""
        db = self._session_factory()
        try:
            job = db.get(BackgroundJobModel, job_id)
            if job is None:
                return
            job.status = "failed"
            job.finished_at = utcnow()
            job.error_message = error_message[:1000]
            db.commit()
        finally:
            db.close()

    def reset_to_pending(self, job_id: str) -> None:
        """将失败任务重置为待执行，等待下次认领重试。"""
        db = self._session_factory()
        try:
            job = db.get(BackgroundJobModel, job_id)
            if job is None:
                return
            job.status = "pending"
            job.started_at = None
            db.commit()
        finally:
            db.close()

    def recover_stuck_jobs(self) -> int:
        """将进程重启后卡在 running 状态的任务标记为失败。

        Returns:
            恢复的任务数量。
        """
        db = self._session_factory()
        try:
            stuck = (
                db.query(BackgroundJobModel)
                .filter(BackgroundJobModel.status == "running")
                .all()
            )
            for job in stuck:
                job.status = "failed"
                job.finished_at = utcnow()
                job.error_message = "进程重启，任务中断"
            if stuck:
                db.commit()
            return len(stuck)
        finally:
            db.close()
