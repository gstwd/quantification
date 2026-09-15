"""后台任务队列仓库。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, false, func, or_
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.infra.db.models.core import BackgroundJobModel
from quant_etf_api.infra.time import utcnow_aware


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
    batch_id: str | None = None
    cancel_requested: bool = False
    created_at: datetime | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None


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
        """按去重键查询未完成（pending/running/paused）的任务。

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
                    BackgroundJobModel.status.in_(["pending", "running", "paused"]),
                )
                .first()
            )
            return row.job_id if row is not None else None
        finally:
            db.close()

    def find_active_payload_by_key(self, job_key: str) -> dict | None:
        """查询去重任务的载荷，用于关联其所属运行记录。

        Args:
            job_key: 去重键。

        Returns:
            未完成任务的载荷副本；不存在时返回 None。
        """
        db = self._session_factory()
        try:
            row = (
                db.query(BackgroundJobModel)
                .filter(
                    BackgroundJobModel.job_key == job_key,
                    BackgroundJobModel.status.in_(["pending", "running", "paused"]),
                )
                .first()
            )
            return dict(row.payload or {}) if row is not None else None
        finally:
            db.close()

    def claim_pending(
        self,
        limit: int = 1,
        job_types: Sequence[str] | None = None,
        exclude_job_types: Sequence[str] | None = None,
    ) -> list[ClaimedJob]:
        """原子认领待执行任务，置为 running 并累计尝试次数。

        使用 PostgreSQL 的 FOR UPDATE SKIP LOCKED 保证多 worker
        并发认领时不会重复执行同一任务。

        Args:
            limit: 单次认领的最大任务数。
            job_types: 任务类型白名单（lane 隔离），None 表示不限类型。
            exclude_job_types: 任务类型黑名单，用于"其余全部类型"的 lane。

        Returns:
            认领成功的任务快照列表（已脱离 Session，可安全读取属性）。
        """
        db = self._session_factory()
        try:
            query = db.query(BackgroundJobModel).filter(BackgroundJobModel.status == "pending")
            if job_types is not None:
                query = query.filter(BackgroundJobModel.job_type.in_(list(job_types)))
            if exclude_job_types is not None:
                query = query.filter(BackgroundJobModel.job_type.notin_(list(exclude_job_types)))
            rows = (
                query.order_by(
                    BackgroundJobModel.priority.desc(),
                    BackgroundJobModel.created_at.asc(),
                    # 次序键兜底：created_at 在同一时间片内可能完全相同
                    # （批量入队 + Windows 时钟精度约 15.6ms），缺少兜底键时
                    # 认领顺序不确定，queue_position 也会与实际认领次序不符
                    BackgroundJobModel.job_id.asc(),
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
                .all()
            )
            now = utcnow_aware()
            claimed: list[ClaimedJob] = []
            for job in rows:
                job.status = "running"
                job.attempts = (job.attempts or 0) + 1
                job.started_at = now
                # 认领即写入首次心跳，僵尸扫描据此判断活性（B7）
                job.heartbeat_at = now
                # 在 Session 关闭前复制字段为快照，避免游离实例访问过期属性
                claimed.append(_snapshot(job))
            db.commit()
            return claimed
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def heartbeat(self, job_id: str) -> None:
        """更新任务心跳时间，供运行期僵尸任务扫描判断活性。"""
        db = self._session_factory()
        try:
            job = db.get(BackgroundJobModel, job_id)
            if job is None or job.status != "running":
                return
            job.heartbeat_at = utcnow_aware()
            db.commit()
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
            job.finished_at = utcnow_aware()
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
            job.finished_at = utcnow_aware()
            job.error_message = error_message[:1000]
            db.commit()
        finally:
            db.close()

    def mark_cancelled(self, job_id: str, message: str = "任务已取消") -> None:
        """将任务标记为已取消。

        Args:
            job_id: 任务标识。
            message: 取消原因，写入 error_message 便于展示。
        """
        db = self._session_factory()
        try:
            job = db.get(BackgroundJobModel, job_id)
            if job is None:
                return
            job.status = "cancelled"
            job.finished_at = utcnow_aware()
            job.error_message = message[:1000]
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
            job.heartbeat_at = None
            db.commit()
        finally:
            db.close()

    def is_cancel_requested(self, job_id: str) -> bool:
        """查询任务是否已被请求取消。

        Args:
            job_id: 任务标识。

        Returns:
            True 表示已请求取消（任务可能仍在运行）。
        """
        db = self._session_factory()
        try:
            requested = (
                db.query(BackgroundJobModel.cancel_requested)
                .filter(BackgroundJobModel.job_id == job_id)
                .scalar()
            )
            return bool(requested)
        finally:
            db.close()

    def request_cancel(self, job_id: str, message: str = "任务已取消") -> str | None:
        """请求取消单个任务。

        未开始（pending/paused）的任务直接置为 cancelled；
        运行中的任务只打取消标记，由处理器在安全检查点协作退出（B2）。

        Args:
            job_id: 任务标识。
            message: 取消原因。

        Returns:
            处理后的任务状态；任务不存在时返回 None。
        """
        db = self._session_factory()
        try:
            job = db.get(BackgroundJobModel, job_id)
            if job is None:
                return None
            if job.status in ("pending", "paused"):
                job.status = "cancelled"
                job.finished_at = utcnow_aware()
                job.error_message = message[:1000]
            elif job.status == "running":
                job.cancel_requested = True
            db.commit()
            return job.status
        finally:
            db.close()

    def cancel_batch(self, batch_id: str, message: str = "批次已取消") -> dict[str, int]:
        """取消整批任务：未开始直接取消，运行中打协作取消标记。

        Args:
            batch_id: 批次标识。
            message: 取消原因。

        Returns:
            {"cancelled": 直接取消数, "requested": 协作取消数}。
        """
        db = self._session_factory()
        try:
            rows = (
                db.query(BackgroundJobModel)
                .filter(
                    BackgroundJobModel.batch_id == batch_id,
                    BackgroundJobModel.status.in_(["pending", "paused", "running"]),
                )
                .all()
            )
            cancelled = 0
            requested = 0
            now = utcnow_aware()
            for job in rows:
                if job.status == "running":
                    job.cancel_requested = True
                    requested += 1
                else:
                    job.status = "cancelled"
                    job.finished_at = now
                    job.error_message = message[:1000]
                    cancelled += 1
            db.commit()
            return {"cancelled": cancelled, "requested": requested}
        finally:
            db.close()

    def pause_batch(self, batch_id: str, message: str = "批次已暂停") -> int:
        """暂停整批任务：仅把 pending 置为 paused，运行中的任务自然结束。

        Args:
            batch_id: 批次标识。
            message: 暂停说明。

        Returns:
            被暂停的任务数。
        """
        db = self._session_factory()
        try:
            rows = (
                db.query(BackgroundJobModel)
                .filter(
                    BackgroundJobModel.batch_id == batch_id,
                    BackgroundJobModel.status == "pending",
                )
                .all()
            )
            for job in rows:
                job.status = "paused"
                job.error_message = message[:1000]
            db.commit()
            return len(rows)
        finally:
            db.close()

    def resume_batch(self, batch_id: str) -> int:
        """恢复整批被暂停的任务。

        Args:
            batch_id: 批次标识。

        Returns:
            被恢复的任务数。
        """
        db = self._session_factory()
        try:
            rows = (
                db.query(BackgroundJobModel)
                .filter(
                    BackgroundJobModel.batch_id == batch_id,
                    BackgroundJobModel.status == "paused",
                )
                .all()
            )
            for job in rows:
                job.status = "pending"
                job.error_message = None
            db.commit()
            return len(rows)
        finally:
            db.close()

    def find_stale_running(self, timeout_seconds: float) -> list[ClaimedJob]:
        """查找心跳超时的 running 任务（僵尸任务）。

        以 heartbeat_at 为准；历史行无心跳时回退到 started_at。

        Args:
            timeout_seconds: 心跳超时阈值（秒）。

        Returns:
            僵尸任务快照列表。
        """
        db = self._session_factory()
        try:
            cutoff = utcnow_aware() - timedelta(seconds=max(1.0, timeout_seconds))
            rows = (
                db.query(BackgroundJobModel)
                .filter(
                    BackgroundJobModel.status == "running",
                    func.coalesce(BackgroundJobModel.heartbeat_at, BackgroundJobModel.started_at)
                    < cutoff,
                )
                .all()
            )
            return [_snapshot(job) for job in rows]
        finally:
            db.close()

    def find_running_over(self, max_runtime_seconds: float) -> list[ClaimedJob]:
        """查找运行时间超过上限的 running 任务（心跳正常但耗时异常）。

        与 ``find_stale_running`` 互补：心跳仍新鲜但已运行过久（例如卡在
        数据库锁上），同样需要回收，避免单条任务永久占用 worker（B7）。

        Args:
            max_runtime_seconds: 单任务最长运行时间（秒）。

        Returns:
            超长运行任务快照列表。
        """
        db = self._session_factory()
        try:
            cutoff = utcnow_aware() - timedelta(seconds=max(1.0, max_runtime_seconds))
            rows = (
                db.query(BackgroundJobModel)
                .filter(
                    BackgroundJobModel.status == "running",
                    BackgroundJobModel.started_at.isnot(None),
                    BackgroundJobModel.started_at < cutoff,
                )
                .all()
            )
            return [_snapshot(job) for job in rows]
        finally:
            db.close()

    def find_snapshots_by_ids(self, job_ids: Sequence[str]) -> dict[str, ClaimedJob]:
        """按任务 ID 批量查询任务快照。

        Args:
            job_ids: 任务 ID 列表。

        Returns:
            job_id → 快照字典（不存在的 ID 不出现在结果中）。
        """
        ids = [job_id for job_id in job_ids if job_id]
        if not ids:
            return {}
        db = self._session_factory()
        try:
            rows = db.query(BackgroundJobModel).filter(BackgroundJobModel.job_id.in_(ids)).all()
            return {row.job_id: _snapshot(row) for row in rows}
        finally:
            db.close()

    def find_snapshots_by_keys(self, job_keys: Sequence[str]) -> dict[str, ClaimedJob]:
        """按去重键批量查询任务快照（同一 key 取最早一条）。

        Args:
            job_keys: 去重键列表。

        Returns:
            job_key → 快照字典。
        """
        keys = [key for key in job_keys if key]
        if not keys:
            return {}
        db = self._session_factory()
        try:
            rows = (
                db.query(BackgroundJobModel)
                .filter(BackgroundJobModel.job_key.in_(keys))
                .order_by(BackgroundJobModel.created_at.asc())
                .all()
            )
            return {row.job_key: _snapshot(row) for row in rows if row.job_key}
        finally:
            db.close()

    def count_pending_ahead(
        self,
        priority: int,
        created_at: datetime,
        job_types: Sequence[str] | None = None,
        job_id: str | None = None,
    ) -> int:
        """统计排在指定任务之前、待执行的任务数（队列位置）。

        排序规则与 claim_pending 一致：优先级降序 + 创建时间升序 + 任务 ID 升序。

        Args:
            priority: 目标任务优先级。
            created_at: 目标任务创建时间。
            job_types: 任务类型白名单（lane 内位置），None 表示全队列。
            job_id: 目标任务 ID；给出时按与 claim_pending 相同的兜底次序键
                精确统计（创建时间在同一时间片内相同时仍能给出一致的排队位置）。

        Returns:
            排在前面的待执行任务数量。
        """
        db = self._session_factory()
        try:
            query = db.query(func.count(BackgroundJobModel.job_id)).filter(
                BackgroundJobModel.status == "pending",
                or_(
                    BackgroundJobModel.priority > priority,
                    and_(
                        BackgroundJobModel.priority == priority,
                        BackgroundJobModel.created_at < created_at,
                    ),
                    and_(
                        BackgroundJobModel.priority == priority,
                        BackgroundJobModel.created_at == created_at,
                        BackgroundJobModel.job_id < job_id,
                    )
                    if job_id is not None
                    else false(),
                ),
            )
            if job_types is not None:
                query = query.filter(BackgroundJobModel.job_type.in_(list(job_types)))
            return int(query.scalar() or 0)
        finally:
            db.close()

    def list_jobs(
        self,
        statuses: Sequence[str] | None = None,
        job_types: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[ClaimedJob]:
        """按状态/类型查询最近的任务快照（按创建时间倒序）。

        Args:
            statuses: 状态白名单，None 表示不限。
            job_types: 类型白名单，None 表示不限。
            limit: 返回条数上限。

        Returns:
            任务快照列表。
        """
        db = self._session_factory()
        try:
            query = db.query(BackgroundJobModel)
            if statuses:
                query = query.filter(BackgroundJobModel.status.in_(list(statuses)))
            if job_types:
                query = query.filter(BackgroundJobModel.job_type.in_(list(job_types)))
            rows = query.order_by(BackgroundJobModel.created_at.desc()).limit(max(1, limit)).all()
            return [_snapshot(row) for row in rows]
        finally:
            db.close()

    def status_counts(self) -> dict[str, int]:
        """按状态统计任务数量。"""
        db = self._session_factory()
        try:
            rows = (
                db.query(BackgroundJobModel.status, func.count(BackgroundJobModel.job_id))
                .group_by(BackgroundJobModel.status)
                .all()
            )
            return {str(status or "unknown"): int(count) for status, count in rows}
        finally:
            db.close()

    def type_backlog(self) -> list[dict[str, object]]:
        """按任务类型统计积压（pending/running/paused）。

        Returns:
            每类任务的统计列表：job_type / pending / running / paused /
            oldest_pending_at。
        """
        db = self._session_factory()
        try:
            rows = (
                db.query(
                    BackgroundJobModel.job_type,
                    BackgroundJobModel.status,
                    func.count(BackgroundJobModel.job_id),
                    func.min(BackgroundJobModel.created_at),
                )
                .filter(BackgroundJobModel.status.in_(["pending", "running", "paused"]))
                .group_by(BackgroundJobModel.job_type, BackgroundJobModel.status)
                .all()
            )
            merged: dict[str, dict[str, object]] = {}
            for job_type, status, count, oldest in rows:
                item = merged.setdefault(
                    str(job_type),
                    {
                        "job_type": str(job_type),
                        "pending": 0,
                        "running": 0,
                        "paused": 0,
                        "oldest_pending_at": None,
                    },
                )
                item[str(status)] = int(count)
                if status == "pending" and oldest is not None:
                    current = item["oldest_pending_at"]
                    if current is None or oldest < current:  # type: ignore[operator]
                        item["oldest_pending_at"] = oldest
            return sorted(merged.values(), key=lambda item: str(item["job_type"]))
        finally:
            db.close()

    def throughput_since(self, since: datetime) -> dict[str, int]:
        """统计指定时间点之后完成的任务数（成功/失败/取消）。

        Args:
            since: 统计起点（UTC aware）。

        Returns:
            {"success": n, "failed": n, "cancelled": n}。
        """
        db = self._session_factory()
        try:
            rows = (
                db.query(BackgroundJobModel.status, func.count(BackgroundJobModel.job_id))
                .filter(
                    BackgroundJobModel.status.in_(["success", "failed", "cancelled"]),
                    BackgroundJobModel.finished_at >= since,
                )
                .group_by(BackgroundJobModel.status)
                .all()
            )
            result = {"success": 0, "failed": 0, "cancelled": 0}
            for status, count in rows:
                result[str(status)] = int(count)
            return result
        finally:
            db.close()

    def recover_stuck_jobs(self, timeout_seconds: float) -> int:
        """将心跳超时、已确认失去执行进程的 running 任务标记为失败。

        **不能**把所有 running 任务都当作残留：运行期启动第二个 worker
        进程（或 API 进程重启而独立 worker 仍在跑）时，无条件清空会把
        别人正在执行的任务写成失败，导致 ``job_key`` 去重失效、同一任务
        被并发执行两份。因此这里复用运行期僵尸扫描的判据——仅当心跳
        （历史行回退到 ``started_at``）超过 ``timeout_seconds`` 未更新，
        才认定执行进程已死，与 ``JobQueue.scan_zombies`` 口径一致。

        Args:
            timeout_seconds: 心跳超时阈值（秒），口径同
                ``job_stuck_timeout_seconds``。

        Returns:
            被判为失败的任务数量。
        """
        db = self._session_factory()
        try:
            cutoff = utcnow_aware() - timedelta(seconds=max(1.0, timeout_seconds))
            stuck = (
                db.query(BackgroundJobModel)
                .filter(
                    BackgroundJobModel.status == "running",
                    func.coalesce(BackgroundJobModel.heartbeat_at, BackgroundJobModel.started_at)
                    < cutoff,
                )
                .all()
            )
            for job in stuck:
                job.status = "failed"
                job.finished_at = utcnow_aware()
                job.error_message = (
                    f"执行进程已中断（心跳超过 {timeout_seconds:.0f} 秒未更新），任务中断"
                )
            if stuck:
                db.commit()
            return len(stuck)
        finally:
            db.close()


def _snapshot(job: BackgroundJobModel) -> ClaimedJob:
    """把 ORM 行复制为脱离 Session 的快照对象。"""
    return ClaimedJob(
        job_id=job.job_id,
        job_type=job.job_type,
        payload=job.payload,
        status=job.status,
        priority=job.priority or 0,
        attempts=job.attempts or 0,
        max_attempts=job.max_attempts or 1,
        error_message=job.error_message,
        batch_id=getattr(job, "batch_id", None),
        cancel_requested=bool(getattr(job, "cancel_requested", False)),
        created_at=job.created_at,
        started_at=job.started_at,
        heartbeat_at=getattr(job, "heartbeat_at", None),
        finished_at=job.finished_at,
    )
