"""稳健性批次控制（取消/暂停/恢复）与部分汇总单元测试（B2/B7）。

用最小替身对象构造 RobustnessService，验证：
- ``collect(allow_partial=True)`` 只按已完成窗口汇总并写入 coverage；
- 存在失败/未完成窗口且未显式允许部分汇总时，批次落为 failed / running；
- ``cancel`` 同时处理队列任务与同步执行的残留回测。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from quant_etf_api.services.robustness_service import RobustnessService


class _FakeVariant:
    """替身批次行：只需 robustness_id/variants/windows/status 等属性。"""

    def __init__(self, variants: list[dict[str, Any]]) -> None:
        self.robustness_id = "rb-1"
        self.strategy_id = "s1"
        self.strategy_version = "1.0.0"
        self.kind = "ablate"
        self.status = "running"
        self.start_date = date(2016, 1, 1)
        self.end_date = date(2025, 12, 31)
        self.windows = [{"label": "W0"}, {"label": "W1"}]
        self.variants = variants
        self.summary: dict[str, Any] | None = None
        self.statistics: dict[str, Any] | None = None
        self.error_message: str | None = None
        self.finished_at = None
        self.updated_at = None
        self.trial_count = 1


class _FakeBacktestRow:
    """替身回测行。"""

    def __init__(self, status: str, metrics: dict[str, Any] | None = None) -> None:
        self.status = status
        self.metrics = metrics or {}


class _FakeBacktestRepo:
    """替身回测仓库：按 id 返回预置状态。"""

    def __init__(self, rows: dict[str, _FakeBacktestRow]) -> None:
        self.rows = rows
        self.cancelled: list[str] = []

    def find_by_id(self, backtest_id: str) -> _FakeBacktestRow | None:
        """按 ID 返回回测行。"""
        return self.rows.get(backtest_id)

    def mark_cancelled(self, backtest_id: str, message: str = "") -> None:
        """记录被落为取消的回测。"""
        self.cancelled.append(backtest_id)
        if backtest_id in self.rows:
            self.rows[backtest_id].status = "cancelled"


class _FakeDB:
    """替身 Session：只记录 commit 调用。"""

    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        """记录一次提交。"""
        self.commits += 1


def _make_service(
    row: _FakeVariant, rows: dict[str, _FakeBacktestRow]
) -> tuple[RobustnessService, _FakeBacktestRepo, _FakeDB]:
    """构造只带必要依赖的 RobustnessService 替身。"""
    service = object.__new__(RobustnessService)
    repo = _FakeBacktestRepo(rows)
    db = _FakeDB()
    service._db = db  # type: ignore[assignment]
    service._backtest_repo = repo  # type: ignore[assignment]
    service._find = lambda robustness_id: row  # type: ignore[method-assign]
    return service, repo, db


def _variants_with_two_windows() -> list[dict[str, Any]]:
    """构造含 baseline 与一个消融变体、各两个窗口的变体列表。"""
    return [
        {
            "label": "baseline",
            "kind": "baseline",
            "knob": None,
            "value": None,
            "backtest_ids": {"W0": "bt-base-0", "W1": "bt-base-1"},
        },
        {
            "label": "ablate_x",
            "kind": "ablation",
            "knob": "score.factors.x",
            "value": None,
            "backtest_ids": {"W0": "bt-ab-0", "W1": "bt-ab-1"},
        },
    ]


class TestCollectPartial:
    """B7：批次因个别窗口卡死/失败时仍可部分收口。"""

    def test_collect_reports_running_while_windows_pending(self) -> None:
        """未显式允许部分汇总时，仍有未完成窗口应返回 running。"""
        row = _FakeVariant(_variants_with_two_windows())
        rows = {
            "bt-base-0": _FakeBacktestRow("success", {"sharpe_ratio": 1.0}),
            "bt-base-1": _FakeBacktestRow("running"),
            "bt-ab-0": _FakeBacktestRow("success", {"sharpe_ratio": 0.9}),
            "bt-ab-1": _FakeBacktestRow("running"),
        }
        service, _, _ = _make_service(row, rows)

        result = service.collect("rb-1")

        assert result["status"] == "running"
        assert result["pending_backtests"] == 2

    def test_collect_without_partial_fails_on_failed_window(self) -> None:
        """存在失败窗口时，默认汇总应把批次标记为 failed。"""
        row = _FakeVariant(_variants_with_two_windows())
        rows = {
            "bt-base-0": _FakeBacktestRow("success"),
            "bt-base-1": _FakeBacktestRow("failed"),
            "bt-ab-0": _FakeBacktestRow("success"),
            "bt-ab-1": _FakeBacktestRow("success"),
        }
        service, _, _ = _make_service(row, rows)

        result = service.collect("rb-1")

        assert result["status"] == "failed"
        assert result["failed"] == ["baseline/W1"]

    def test_allow_partial_summarizes_and_records_coverage(self) -> None:
        """allow_partial 时应按已完成窗口汇总并显式记录覆盖率。"""
        row = _FakeVariant(_variants_with_two_windows())
        rows = {
            "bt-base-0": _FakeBacktestRow(
                "success", {"sharpe_ratio": 1.0, "annualized_return_pct": 8.0}
            ),
            "bt-base-1": _FakeBacktestRow("cancelled"),
            "bt-ab-0": _FakeBacktestRow(
                "success", {"sharpe_ratio": 0.8, "annualized_return_pct": 6.0}
            ),
            "bt-ab-1": _FakeBacktestRow(
                "success", {"sharpe_ratio": 0.6, "annualized_return_pct": 4.0}
            ),
        }
        service, _, _ = _make_service(row, rows)

        result = service.collect("rb-1", allow_partial=True)

        assert result["status"] == "partial"
        coverage = result["summary"]["coverage"]
        assert coverage["is_partial"] is True
        assert coverage["expected_windows"] == 4
        assert coverage["completed_windows"] == 3
        assert coverage["failed_windows"] == ["baseline/W1"]
        assert row.status == "partial"
        assert row.finished_at is not None


class TestBatchControl:
    """B2：批次取消。"""

    def test_cancel_marks_leftover_pending_backtests(self, monkeypatch) -> None:
        """取消批次时应把任务已取消、永远不会再跑的 pending 回测落为 cancelled。"""

        class _FakeQueue:
            """替身队列：记录批次取消并在运行中任务上打标记。"""

            def __init__(self) -> None:
                self.calls: list[str] = []

            def cancel_batch(self, batch_id: str, message: str = "") -> dict[str, int]:
                """记录批次取消调用。"""
                self.calls.append(batch_id)
                return {"cancelled": 1, "requested": 1}

        fake_queue = _FakeQueue()
        monkeypatch.setattr("quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue)
        row = _FakeVariant(_variants_with_two_windows())
        rows = {
            "bt-base-0": _FakeBacktestRow("running"),
            "bt-base-1": _FakeBacktestRow("success"),
            "bt-ab-0": _FakeBacktestRow("pending"),
            "bt-ab-1": _FakeBacktestRow("success"),
        }
        service, repo, _ = _make_service(row, rows)

        result = service.cancel("rb-1")

        assert fake_queue.calls == ["rb-1"]
        # 运行中的回测交给协作取消路径，不在这里提前改写状态
        assert repo.cancelled == ["bt-ab-0"]
        assert rows["bt-base-0"].status == "running"
        assert result["status"] == "cancelled"
        assert result["cancelled_jobs"] == 1
        assert result["cancel_requested_jobs"] == 1

    def test_cancel_unknown_batch_raises(self) -> None:
        """批次不存在时应抛出 ValueError。"""
        row = _FakeVariant([])
        service, _, _ = _make_service(row, {})
        service._find = lambda robustness_id: None  # type: ignore[method-assign]

        with pytest.raises(ValueError):
            service.cancel("missing")
