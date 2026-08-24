"""后台任务队列单元测试。

使用内存版 FakeJobRepository 验证入队、去重、认领、执行、
失败重试与卡死恢复逻辑，不依赖真实数据库。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quant_etf_api.infra.db.models.core import BackgroundJobModel
from quant_etf_api.infra.job_queue.handlers import JOB_HANDLERS
from quant_etf_api.infra.job_queue.repository import ClaimedJob
from quant_etf_api.infra.job_queue.queue import JobQueue


class FakeJobRepository:
    """内存版任务仓库，模拟 JobRepository 的公开接口。"""

    def __init__(self) -> None:
        self.jobs: dict[str, BackgroundJobModel] = {}

    def create(self, job: BackgroundJobModel) -> None:
        """写入任务记录。"""
        if job.created_at is None:
            job.created_at = datetime.now(timezone.utc)
        self.jobs[job.job_id] = job

    def find_active_by_key(self, job_key: str) -> str | None:
        """按去重键查找未完成任务，返回 job_id。"""
        for job in self.jobs.values():
            if job.job_key == job_key and job.status in ("pending", "running"):
                return job.job_id
        return None

    def claim_pending(self, limit: int = 1) -> list[ClaimedJob]:
        """认领待执行任务并置为 running，返回快照列表。"""
        pending = sorted(
            [j for j in self.jobs.values() if j.status == "pending"],
            key=lambda j: (-(j.priority or 0), j.created_at),
        )[:limit]
        claimed: list[ClaimedJob] = []
        for job in pending:
            job.status = "running"
            job.attempts = (job.attempts or 0) + 1
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
        return claimed

    def mark_success(self, job_id: str) -> None:
        """标记任务成功。"""
        self.jobs[job_id].status = "success"
        self.jobs[job_id].finished_at = datetime.now(timezone.utc)

    def mark_failed(self, job_id: str, error_message: str) -> None:
        """标记任务失败。"""
        self.jobs[job_id].status = "failed"
        self.jobs[job_id].error_message = error_message
        self.jobs[job_id].finished_at = datetime.now(timezone.utc)

    def reset_to_pending(self, job_id: str) -> None:
        """将任务重置为待执行。"""
        self.jobs[job_id].status = "pending"
        self.jobs[job_id].started_at = None

    def recover_stuck_jobs(self) -> int:
        """将 running 状态任务标记为失败。"""
        count = 0
        for job in self.jobs.values():
            if job.status == "running":
                job.status = "failed"
                job.error_message = "进程重启，任务中断"
                count += 1
        return count


def _make_queue(repo: FakeJobRepository) -> JobQueue:
    """构建测试用 JobQueue。"""
    return JobQueue(repo=repo, workers=1, poll_interval=0.01)


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

        first = queue.enqueue("data_fill", {"resource": "bars", "code": "510300"}, job_key="bars:510300")
        second = queue.enqueue("data_fill", {"resource": "bars", "code": "510300"}, job_key="bars:510300")

        assert first == second
        assert len(repo.jobs) == 1

    def test_execute_success(self, monkeypatch) -> None:
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

    def test_execute_failure_retries_below_max_attempts(self, monkeypatch) -> None:
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

    def test_execute_failure_marks_failed_when_attempts_exhausted(self, monkeypatch) -> None:
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
        """进程重启后 running 任务应恢复为 failed。"""
        repo = FakeJobRepository()
        queue = _make_queue(repo)
        job_id = queue.enqueue("daily_ingest", {})

        repo.claim_pending(1)
        assert repo.jobs[job_id].status == "running"

        count = queue.recover_stuck_jobs()

        assert count == 1
        assert repo.jobs[job_id].status == "failed"
        assert "进程重启" in (repo.jobs[job_id].error_message or "")
