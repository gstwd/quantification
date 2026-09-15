"""后台任务队列单元测试（B1/B2/B3/B6/B7）。

使用内存版 FakeJobRepository 验证入队、去重、lane 隔离、优先级、认领、执行、
失败重试、批次取消/暂停、心跳超时回收与统计口径，不依赖真实数据库。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from quant_etf_api.infra.db.models.core import BackgroundJobModel
from quant_etf_api.infra.job_queue.context import JobCancelledError, clear_cache
from quant_etf_api.infra.job_queue.handlers import JOB_ABANDON_HANDLERS, JOB_HANDLERS
from quant_etf_api.infra.job_queue.queue import (
    BACKTEST_LANE_JOB_TYPES,
    LANE_BACKTEST,
    LANE_GENERAL,
    JobQueue,
    backtest_job_key,
    lane_for,
)
from quant_etf_api.infra.job_queue.repository import ClaimedJob


def _now() -> datetime:
    """返回当前 UTC aware 时间（与 timestamptz 列口径一致）。"""
    return datetime.now(timezone.utc)


class FakeJobRepository:
    """内存版任务仓库，模拟 JobRepository 的公开接口。"""

    def __init__(self) -> None:
        self.jobs: dict[str, BackgroundJobModel] = {}
        self.heartbeats: list[str] = []

    # ── 写入与查询 ───────────────────────────────────────────────────────

    def create(self, job: BackgroundJobModel) -> None:
        """写入任务记录。

        created_at 用"每插入一条前进 1 微秒"的单调时间：Windows 上
        ``datetime.now()`` 的精度约 15.6ms，连续入队会拿到完全相同的时间戳，
        把"按创建时间排队"的用例变成不确定行为（真实库侧由
        ``claim_pending`` 的 job_id 兜底次序键解决，这里保证用例可复现）。
        """
        if job.created_at is None:
            job.created_at = _now() + timedelta(microseconds=len(self.jobs))
        if getattr(job, "cancel_requested", None) is None:
            job.cancel_requested = False
        self.jobs[job.job_id] = job

    def find_active_by_key(self, job_key: str) -> str | None:
        """按去重键查找未完成任务，返回 job_id。"""
        for job in self.jobs.values():
            if job.job_key == job_key and job.status in ("pending", "running", "paused"):
                return job.job_id
        return None

    def find_active_payload_by_key(self, job_key: str) -> dict | None:
        """按去重键返回未完成任务的载荷。"""
        job_id = self.find_active_by_key(job_key)
        if job_id is None:
            return None
        return dict(self.jobs[job_id].payload or {})

    def claim_pending(
        self,
        limit: int = 1,
        job_types: tuple[str, ...] | None = None,
        exclude_job_types: tuple[str, ...] | None = None,
    ) -> list[ClaimedJob]:
        """认领待执行任务并置为 running，返回快照列表。"""
        pending = [j for j in self.jobs.values() if j.status == "pending"]
        if job_types is not None:
            pending = [j for j in pending if j.job_type in job_types]
        if exclude_job_types is not None:
            pending = [j for j in pending if j.job_type not in exclude_job_types]
        pending.sort(key=lambda j: (-(j.priority or 0), j.created_at))
        claimed: list[ClaimedJob] = []
        for job in pending[:limit]:
            job.status = "running"
            job.attempts = (job.attempts or 0) + 1
            job.started_at = _now()
            job.heartbeat_at = job.started_at
            claimed.append(self._snapshot(job))
        return claimed

    def heartbeat(self, job_id: str) -> None:
        """更新任务心跳时间。"""
        job = self.jobs.get(job_id)
        if job is None or job.status != "running":
            return
        job.heartbeat_at = _now()
        self.heartbeats.append(job_id)

    def mark_success(self, job_id: str) -> None:
        """标记任务成功。"""
        self.jobs[job_id].status = "success"
        self.jobs[job_id].finished_at = _now()

    def mark_failed(self, job_id: str, error_message: str) -> None:
        """标记任务失败。"""
        self.jobs[job_id].status = "failed"
        self.jobs[job_id].error_message = error_message
        self.jobs[job_id].finished_at = _now()

    def mark_cancelled(self, job_id: str, message: str = "任务已取消") -> None:
        """标记任务已取消。"""
        self.jobs[job_id].status = "cancelled"
        self.jobs[job_id].error_message = message
        self.jobs[job_id].finished_at = _now()

    def reset_to_pending(self, job_id: str) -> None:
        """将任务重置为待执行。"""
        self.jobs[job_id].status = "pending"
        self.jobs[job_id].started_at = None
        self.jobs[job_id].heartbeat_at = None

    def is_cancel_requested(self, job_id: str) -> bool:
        """查询任务是否已被请求取消。"""
        job = self.jobs.get(job_id)
        return bool(job is not None and job.cancel_requested)

    def request_cancel(self, job_id: str, message: str = "任务已取消") -> str | None:
        """请求取消单个任务。"""
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job.status in ("pending", "paused"):
            job.status = "cancelled"
            job.error_message = message
            job.finished_at = _now()
        elif job.status == "running":
            job.cancel_requested = True
        return job.status

    def cancel_batch(self, batch_id: str, message: str = "批次已取消") -> dict[str, int]:
        """取消整批任务。"""
        cancelled = 0
        requested = 0
        for job in self.jobs.values():
            if job.batch_id != batch_id or job.status not in ("pending", "paused", "running"):
                continue
            if job.status == "running":
                job.cancel_requested = True
                requested += 1
            else:
                job.status = "cancelled"
                job.error_message = message
                job.finished_at = _now()
                cancelled += 1
        return {"cancelled": cancelled, "requested": requested}

    def pause_batch(self, batch_id: str, message: str = "批次已暂停") -> int:
        """暂停整批待执行任务。"""
        count = 0
        for job in self.jobs.values():
            if job.batch_id == batch_id and job.status == "pending":
                job.status = "paused"
                job.error_message = message
                count += 1
        return count

    def resume_batch(self, batch_id: str) -> int:
        """恢复整批被暂停的任务。"""
        count = 0
        for job in self.jobs.values():
            if job.batch_id == batch_id and job.status == "paused":
                job.status = "pending"
                job.error_message = None
                count += 1
        return count

    def find_stale_running(self, timeout_seconds: float) -> list[ClaimedJob]:
        """查找心跳超时的 running 任务。"""
        cutoff = _now() - timedelta(seconds=timeout_seconds)
        return [
            self._snapshot(job)
            for job in self.jobs.values()
            if job.status == "running"
            and (job.heartbeat_at or job.started_at) is not None
            and (job.heartbeat_at or job.started_at) < cutoff
        ]

    def find_running_over(self, max_runtime_seconds: float) -> list[ClaimedJob]:
        """查找运行时间超过上限的任务。"""
        cutoff = _now() - timedelta(seconds=max_runtime_seconds)
        return [
            self._snapshot(job)
            for job in self.jobs.values()
            if job.status == "running" and job.started_at is not None and job.started_at < cutoff
        ]

    def find_snapshots_by_ids(self, job_ids: list[str]) -> dict[str, ClaimedJob]:
        """按 ID 批量查询快照。"""
        return {
            job_id: self._snapshot(self.jobs[job_id]) for job_id in job_ids if job_id in self.jobs
        }

    def find_snapshots_by_keys(self, job_keys: list[str]) -> dict[str, ClaimedJob]:
        """按去重键批量查询快照。"""
        result: dict[str, ClaimedJob] = {}
        for job in sorted(self.jobs.values(), key=lambda j: j.created_at):
            if job.job_key in job_keys:
                result[job.job_key] = self._snapshot(job)
        return result

    def count_pending_ahead(
        self,
        priority: int,
        created_at: datetime,
        job_types: tuple[str, ...] | None = None,
        job_id: str | None = None,
    ) -> int:
        """统计排在指定任务之前的待执行任务数（次序键与 claim_pending 一致）。"""
        count = 0
        for job in self.jobs.values():
            if job.status != "pending":
                continue
            if job_types is not None and job.job_type not in job_types:
                continue
            job_priority = job.priority or 0
            if job_priority > priority:
                count += 1
                continue
            if job_priority < priority:
                continue
            if job.created_at < created_at:
                count += 1
                continue
            if job.created_at == created_at and job_id is not None and job.job_id < job_id:
                count += 1
        return count

    def list_jobs(
        self,
        statuses: list[str] | None = None,
        job_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[ClaimedJob]:
        """按状态/类型返回最近任务快照。"""
        rows = [
            job
            for job in self.jobs.values()
            if (not statuses or job.status in statuses)
            and (not job_types or job.job_type in job_types)
        ]
        rows.sort(key=lambda j: j.created_at, reverse=True)
        return [self._snapshot(job) for job in rows[:limit]]

    def status_counts(self) -> dict[str, int]:
        """按状态统计任务数量。"""
        counts: dict[str, int] = {}
        for job in self.jobs.values():
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts

    def type_backlog(self) -> list[dict[str, Any]]:
        """按类型统计积压。"""
        merged: dict[str, dict[str, Any]] = {}
        for job in self.jobs.values():
            if job.status not in ("pending", "running", "paused"):
                continue
            item = merged.setdefault(
                job.job_type,
                {
                    "job_type": job.job_type,
                    "pending": 0,
                    "running": 0,
                    "paused": 0,
                    "oldest_pending_at": None,
                },
            )
            item[job.status] += 1
            if job.status == "pending":
                oldest = item["oldest_pending_at"]
                if oldest is None or job.created_at < oldest:
                    item["oldest_pending_at"] = job.created_at
        return list(merged.values())

    def throughput_since(self, since: datetime) -> dict[str, int]:
        """统计窗口内完成的任务数。"""
        result = {"success": 0, "failed": 0, "cancelled": 0}
        for job in self.jobs.values():
            if job.status in result and job.finished_at is not None and job.finished_at >= since:
                result[job.status] += 1
        return result

    def recover_stuck_jobs(self, timeout_seconds: float) -> int:
        """将心跳超时的 running 任务标记为失败（判据同 find_stale_running）。"""
        cutoff = _now() - timedelta(seconds=timeout_seconds)
        count = 0
        for job in self.jobs.values():
            if job.status != "running":
                continue
            heartbeat = job.heartbeat_at or job.started_at
            if heartbeat is None or heartbeat >= cutoff:
                continue
            job.status = "failed"
            job.error_message = "执行进程已中断，任务中断"
            count += 1
        return count

    @staticmethod
    def _snapshot(job: BackgroundJobModel) -> ClaimedJob:
        """把 ORM 行复制为快照对象。"""
        return ClaimedJob(
            job_id=job.job_id,
            job_type=job.job_type,
            payload=job.payload,
            status=job.status,
            priority=job.priority or 0,
            attempts=job.attempts or 0,
            max_attempts=job.max_attempts or 1,
            error_message=job.error_message,
            batch_id=job.batch_id,
            cancel_requested=bool(job.cancel_requested),
            created_at=job.created_at,
            started_at=job.started_at,
            heartbeat_at=job.heartbeat_at,
            finished_at=job.finished_at,
        )


def _make_queue(repo: FakeJobRepository, **kwargs: Any) -> JobQueue:
    """构建测试用 JobQueue（默认关闭后台扫描线程）。"""
    params: dict[str, Any] = {
        "repo": repo,
        "workers": 1,
        "poll_interval": 0.01,
        "zombie_scan_enabled": False,
    }
    params.update(kwargs)
    return JobQueue(**params)


@pytest.fixture(autouse=True)
def _clean_cancel_cache() -> Any:
    """每个用例前后清理取消状态缓存，避免跨用例串味。"""
    clear_cache()
    yield
    clear_cache()


class TestJobQueue:
    """任务队列核心行为测试。"""

    def test_enqueue_creates_pending_job(self) -> None:
        """入队后应生成一条 pending 状态的任务记录。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)

        job_id = queue.enqueue("daily_ingest", {"run_id": "run-1"})

        assert job_id in repo.jobs
        job = repo.jobs[job_id]
        assert job.job_type == "daily_ingest"
        assert job.payload == {"run_id": "run-1"}
        assert job.status == "pending"

    def test_enqueue_dedup_by_job_key(self) -> None:
        """相同 job_key 且未完成时应幂等返回既有任务。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)

        first = queue.enqueue(
            "data_fill", {"resource": "bars", "code": "510300"}, job_key="bars:510300"
        )
        second = queue.enqueue(
            "data_fill", {"resource": "bars", "code": "510300"}, job_key="bars:510300"
        )

        assert first == second
        assert len(repo.jobs) == 1

    def test_enqueue_with_status_reports_deduplicated_request(self) -> None:
        """验证调用方可识别未新建的去重任务，避免遗留 pending 运行记录。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)

        first_id, first_created = queue.enqueue_with_status(
            "data_manage_operation", {"run_id": "run-1"}, job_key="data_manage:check:all:all"
        )
        second_id, second_created = queue.enqueue_with_status(
            "data_manage_operation", {"run_id": "run-2"}, job_key="data_manage:check:all:all"
        )

        assert first_created is True
        assert second_created is False
        assert first_id == second_id

    def test_backtest_job_key_is_stable(self) -> None:
        """回测任务去重键必须稳定，观测接口依赖它反查任务。"""
        assert backtest_job_key("abc") == "backtest:abc"

    def test_execute_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """处理器执行成功后任务应标记为 success。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        calls: list[dict[str, Any]] = []

        def handler(payload: dict) -> None:
            calls.append(payload)

        monkeypatch.setitem(JOB_HANDLERS, "test_job", handler)
        job_id = queue.enqueue("test_job", {"x": 1})

        claimed = repo.claim_pending(1)
        assert claimed[0].status == "running"
        assert claimed[0].attempts == 1

        queue._execute(claimed[0])

        assert repo.jobs[job_id].status == "success"
        assert calls == [{"x": 1}]

    def test_execute_failure_retries_below_max_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """失败且未超过 max_attempts 时应重置为 pending 等待重试。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)

        def handler(payload: dict) -> None:
            raise RuntimeError("boom")

        monkeypatch.setitem(JOB_HANDLERS, "test_job", handler)
        job_id = queue.enqueue("test_job", {}, max_attempts=3)

        for expected_attempt in (1, 2):
            claimed = repo.claim_pending(1)
            assert claimed[0].attempts == expected_attempt
            queue._execute(claimed[0])
            assert repo.jobs[job_id].status == "pending"

        claimed = repo.claim_pending(1)
        assert claimed[0].attempts == 3
        queue._execute(claimed[0])
        assert repo.jobs[job_id].status == "failed"
        assert "boom" in (repo.jobs[job_id].error_message or "")

    def test_execute_failure_marks_failed_when_attempts_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """max_attempts=1 时首次失败即标记为 failed。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)

        def handler(payload: dict) -> None:
            raise ValueError("bad")

        monkeypatch.setitem(JOB_HANDLERS, "test_job", handler)
        job_id = queue.enqueue("test_job", {}, max_attempts=1)

        claimed = repo.claim_pending(1)
        queue._execute(claimed[0])

        assert repo.jobs[job_id].status == "failed"
        assert "bad" in (repo.jobs[job_id].error_message or "")

    def test_unknown_job_type_marks_failed(self) -> None:
        """未知任务类型应标记为 failed 并记录错误。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)

        job_id = queue.enqueue("unknown_type", {})
        claimed = repo.claim_pending(1)
        queue._execute(claimed[0])

        assert repo.jobs[job_id].status == "failed"
        assert "未知任务类型" in (repo.jobs[job_id].error_message or "")

    def test_recover_stuck_jobs(self) -> None:
        """进程重启后心跳超时的 running 任务应恢复为 failed。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo, stuck_timeout=60.0)
        job_id = queue.enqueue("daily_ingest", {})

        repo.claim_pending(1)
        repo.jobs[job_id].heartbeat_at = _now() - timedelta(seconds=600)
        assert repo.jobs[job_id].status == "running"

        count = queue.recover_stuck_jobs()

        assert count == 1
        assert repo.jobs[job_id].status == "failed"
        assert "中断" in (repo.jobs[job_id].error_message or "")

    def test_recover_keeps_running_job_with_fresh_heartbeat(self) -> None:
        """心跳新鲜的 running 任务不得被启动恢复误伤。

        运行期启动第二个 worker（或重启 API）时，另一个进程正在执行的
        任务心跳是新鲜的；若判为失败，其 job_key 去重会失效，同一回测
        会被并发执行两份。
        """
        repo = FakeJobRepository()
        queue = _make_queue(repo, stuck_timeout=60.0)
        job_id = queue.enqueue("backtest", {"backtest_id": "b1"}, job_key="backtest:b1")

        repo.claim_pending(1, job_types=BACKTEST_LANE_JOB_TYPES)
        assert repo.jobs[job_id].status == "running"

        count = queue.recover_stuck_jobs()

        assert count == 0
        assert repo.jobs[job_id].status == "running"
        # 去重仍然生效：同一 job_key 不会因为误判失败而被重复入队
        assert queue.enqueue("backtest", {"backtest_id": "b1"}, job_key="backtest:b1") == job_id

    def test_recover_uses_configured_stuck_timeout(self) -> None:
        """恢复阈值应取队列配置的 stuck_timeout，而非固定值。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo, stuck_timeout=30.0)
        job_id = queue.enqueue("daily_ingest", {})
        repo.claim_pending(1)
        repo.jobs[job_id].heartbeat_at = _now() - timedelta(seconds=120)

        assert queue.recover_stuck_jobs() == 1
        assert repo.jobs[job_id].status == "failed"


class TestLaneIsolation:
    """B1/B6：回测 lane 与通用 lane 的 worker 不能互相抢任务。"""

    def test_lane_for_classifies_backtest_types(self) -> None:
        """回测与对比任务归入回测 lane，其余归入通用 lane。"""
        assert lane_for("backtest") == LANE_BACKTEST
        assert lane_for("comparison") == LANE_BACKTEST
        assert lane_for("daily_ingest") == LANE_GENERAL
        assert lane_for("factor_computation") == LANE_GENERAL

    def test_backtest_lane_only_claims_backtest_jobs(self) -> None:
        """回测 lane 的认领不得取走通用任务（避免摄取被排到队尾）。"""
        repo = FakeJobRepository()
        repo.create(BackgroundJobModel(job_id="j1", job_type="daily_ingest", status="pending"))
        repo.create(BackgroundJobModel(job_id="j2", job_type="backtest", status="pending"))

        claimed = repo.claim_pending(1, job_types=BACKTEST_LANE_JOB_TYPES)

        assert [job.job_id for job in claimed] == ["j2"]

    def test_general_lane_skips_backtest_jobs(self) -> None:
        """通用 lane 的认领必须排除回测任务，保证回测并发预算独立。"""
        repo = FakeJobRepository()
        repo.create(BackgroundJobModel(job_id="j1", job_type="backtest", status="pending"))
        repo.create(BackgroundJobModel(job_id="j2", job_type="daily_ingest", status="pending"))

        claimed = repo.claim_pending(1, exclude_job_types=BACKTEST_LANE_JOB_TYPES)

        assert [job.job_id for job in claimed] == ["j2"]

    def test_priority_preempts_earlier_job(self) -> None:
        """高优先级任务应抢在更早入队的低优先级任务之前执行（B2）。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        queue.enqueue("backtest", {"backtest_id": "low"})
        queue.enqueue("backtest", {"backtest_id": "high"}, priority=5)

        claimed = repo.claim_pending(1, job_types=BACKTEST_LANE_JOB_TYPES)

        assert claimed[0].payload == {"backtest_id": "high"}


class TestBatchControl:
    """B2：批次优先级 / 取消 / 暂停。"""

    def test_cancel_pending_job_marks_cancelled(self) -> None:
        """未开始的任务被取消后直接落为 cancelled。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        job_id = queue.enqueue("backtest", {"backtest_id": "b1"})

        status = queue.cancel_job(job_id)

        assert status == "cancelled"
        assert repo.jobs[job_id].status == "cancelled"

    def test_cancel_running_job_requests_cooperative_exit(self) -> None:
        """运行中的任务只打取消标记，由处理器在安全检查点退出。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        job_id = queue.enqueue("backtest", {"backtest_id": "b1"})
        repo.claim_pending(1)

        status = queue.cancel_job(job_id)

        assert status == "running"
        assert queue.is_cancel_requested(job_id) is True
        assert repo.jobs[job_id].status == "running"

    def test_execute_marks_cancelled_when_handler_raises_cancel(self) -> None:
        """处理器抛出 JobCancelledError 时任务应落为 cancelled 而非 failed。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        job_id = queue.enqueue("backtest", {"backtest_id": "b1"})
        claimed = repo.claim_pending(1)
        queue.cancel_job(job_id)

        def handler(payload: dict) -> None:
            raise JobCancelledError("任务已按请求取消")

        import quant_etf_api.infra.job_queue.handlers as handlers_module

        original = handlers_module.JOB_HANDLERS.get("backtest")
        handlers_module.JOB_HANDLERS["backtest"] = handler
        try:
            queue._execute(claimed[0])
        finally:
            if original is not None:
                handlers_module.JOB_HANDLERS["backtest"] = original
            else:
                handlers_module.JOB_HANDLERS.pop("backtest", None)

        assert repo.jobs[job_id].status == "cancelled"

    def test_cancel_batch_cancels_pending_and_flags_running(self) -> None:
        """整批取消应同时处理待执行与运行中的任务。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        running_id = queue.enqueue("backtest", {"backtest_id": "r"}, batch_id="rb1")
        pending_id = queue.enqueue("backtest", {"backtest_id": "p"}, batch_id="rb1")
        other_id = queue.enqueue("backtest", {"backtest_id": "o"}, batch_id="rb2")
        repo.claim_pending(1, job_types=BACKTEST_LANE_JOB_TYPES)

        result = queue.cancel_batch("rb1")

        assert result == {"cancelled": 1, "requested": 1}
        assert repo.jobs[pending_id].status == "cancelled"
        assert repo.jobs[running_id].cancel_requested is True
        assert repo.jobs[other_id].status == "pending"

    def test_pause_and_resume_batch(self) -> None:
        """暂停后任务不再被认领，恢复后重新变为待执行。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        job_id = queue.enqueue("backtest", {"backtest_id": "b1"}, batch_id="rb1")

        assert queue.pause_batch("rb1") == 1
        assert repo.jobs[job_id].status == "paused"
        assert repo.claim_pending(1, job_types=BACKTEST_LANE_JOB_TYPES) == []

        assert queue.resume_batch("rb1") == 1
        assert repo.jobs[job_id].status == "pending"
        assert len(repo.claim_pending(1, job_types=BACKTEST_LANE_JOB_TYPES)) == 1


class TestZombieScan:
    """B7：心跳超时与超长运行任务的回收。"""

    def test_scan_recovers_stale_running_job_as_failed(self) -> None:
        """心跳超时且尝试次数用尽的任务应被判失败，避免永久占用 worker。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo, stuck_timeout=60.0, max_runtime=0)
        job_id = queue.enqueue("backtest", {"backtest_id": "b1"})
        repo.claim_pending(1)
        repo.jobs[job_id].heartbeat_at = _now() - timedelta(seconds=600)

        recovered = queue.scan_zombies()

        assert recovered == 1
        assert repo.jobs[job_id].status == "failed"
        assert "心跳超时" in (repo.jobs[job_id].error_message or "")

    def test_scan_resets_stale_job_when_attempts_remain(self) -> None:
        """仍有重试次数时卡死任务应回到 pending 而非直接失败。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo, stuck_timeout=60.0, max_runtime=0)
        job_id = queue.enqueue("backtest", {"backtest_id": "b1"}, max_attempts=2)
        repo.claim_pending(1)
        repo.jobs[job_id].heartbeat_at = _now() - timedelta(seconds=600)

        queue.scan_zombies()

        assert repo.jobs[job_id].status == "pending"
        assert repo.jobs[job_id].started_at is None

    def test_scan_recovers_overrun_job_even_with_fresh_heartbeat(self) -> None:
        """心跳正常但运行超时的任务同样要被回收（卡在数据库锁的场景）。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo, stuck_timeout=3600.0, max_runtime=60.0)
        job_id = queue.enqueue("backtest", {"backtest_id": "b1"})
        repo.claim_pending(1)
        repo.jobs[job_id].heartbeat_at = _now()
        repo.jobs[job_id].started_at = _now() - timedelta(seconds=600)

        recovered = queue.scan_zombies()

        assert recovered == 1
        assert repo.jobs[job_id].status == "failed"
        assert "上限" in (repo.jobs[job_id].error_message or "")

    def test_scan_notifies_business_side(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回收异常任务时必须回调业务侧清理，避免回测记录永远停在 running。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo, stuck_timeout=60.0, max_runtime=0)
        job_id = queue.enqueue("backtest", {"backtest_id": "b1"})
        repo.claim_pending(1)
        repo.jobs[job_id].heartbeat_at = _now() - timedelta(seconds=600)
        notified: list[tuple[dict, str]] = []

        monkeypatch.setitem(
            JOB_ABANDON_HANDLERS,
            "backtest",
            lambda payload, reason: notified.append((payload, reason)),
        )

        queue.scan_zombies()

        assert notified and notified[0][0] == {"backtest_id": "b1"}


class TestObservability:
    """B3：队列统计与排队位置。"""

    def test_stats_reports_backlog_and_capacity(self) -> None:
        """统计应给出状态分布、按类型积压、运行中任务与并发预算。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo, workers=3, backtest_workers=1, stuck_timeout=120.0)
        queue.enqueue("backtest", {"backtest_id": "b1"}, batch_id="rb1")
        queue.enqueue("backtest", {"backtest_id": "b2"}, batch_id="rb1")
        repo.claim_pending(1, job_types=BACKTEST_LANE_JOB_TYPES)

        stats = queue.stats(window_hours=1.0)

        assert stats["pending"] == 1
        assert stats["running"] == 1
        assert stats["capacity"]["general_workers"] == 3
        assert stats["capacity"]["backtest_workers"] == 1
        assert stats["capacity"]["stuck_timeout_seconds"] == 120.0
        assert stats["running_jobs"][0]["lane"] == LANE_BACKTEST
        backlog = {item["job_type"]: item for item in stats["backlog_by_type"]}
        assert backlog["backtest"]["pending"] == 1
        assert backlog["backtest"]["running"] == 1

    def test_queue_position_counts_earlier_pending_jobs(self) -> None:
        """排队位置应为该任务之前待执行的同 lane 任务数。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        queue.enqueue("backtest", {"backtest_id": "b1"}, job_key=backtest_job_key("b1"))
        queue.enqueue("backtest", {"backtest_id": "b2"}, job_key=backtest_job_key("b2"))
        queue.enqueue("backtest", {"backtest_id": "b3"}, job_key=backtest_job_key("b3"))
        queue.enqueue("daily_ingest", {"run_id": "r1"})
        snapshot = queue.find_snapshots_by_keys([backtest_job_key("b3")])[backtest_job_key("b3")]

        position = queue.queue_position(snapshot)

        # b1、b2 在回测 lane 中排在前面；通用 lane 的摄取任务不计入
        assert position == 2

    def test_list_jobs_filters_by_status(self) -> None:
        """任务明细支持按状态过滤。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        queue.enqueue("backtest", {"backtest_id": "b1"})
        done = queue.enqueue("daily_ingest", {"run_id": "r1"})
        repo.mark_success(done)

        running = queue.list_jobs(statuses=["pending"])
        success = queue.list_jobs(statuses=["success"])

        assert [item.job_type for item in running] == ["backtest"]
        assert [item.job_type for item in success] == ["daily_ingest"]
