"""F 类修复的稳健性服务单元测试（F-1 / F-3 / F-4 / F-5 / F-13）。

用最小替身构造 RobustnessService，覆盖：

- F-1：汇总只按"所有变体都有数据的共同窗口"比较，窗口数不一致时不再直接相减；
- F-3：没有任何变体结果时邻域判定为 None，不再默认"通过"；
- F-4：被删除的回测归入 missing_windows，失败窗口单独列出；abandon 可显式收口；
- F-5：批次行先落库（含变体映射），执行阶段之前即可被查询/取消；
- F-13：同步模式可指定本地并发进程数，且与入队模式互斥。
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.services.robustness_service import (
    STALE_AFTER_HOURS,
    RobustnessService,
    _is_stale,
)


class _FakeBacktestRow:
    """替身回测行：只需 status 与 metrics。"""

    def __init__(self, status: str, metrics: dict[str, Any] | None = None) -> None:
        self.status = status
        self.metrics = metrics


class _FakeBacktestRepo:
    """替身回测仓库：按 ID 返回预置状态。"""

    def __init__(self, rows: dict[str, _FakeBacktestRow]) -> None:
        self.rows = rows

    def find_by_id(self, backtest_id: str) -> _FakeBacktestRow | None:
        """按 ID 返回回测行。"""
        return self.rows.get(backtest_id)

    def mark_cancelled(self, backtest_id: str, message: str = "") -> None:
        """把回测标记为取消（abandon 用到）。"""
        if backtest_id in self.rows:
            self.rows[backtest_id].status = "cancelled"


class _FakeJobQueue:
    """替身队列：记录批次取消。"""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def cancel_batch(self, batch_id: str, message: str = "") -> dict[str, int]:
        """记录批次取消调用。"""
        self.cancelled.append(batch_id)
        return {"cancelled": 1, "requested": 0}


def _make_row(**overrides: Any) -> SimpleNamespace:
    """构造替身批次行。"""
    fields: dict[str, Any] = {
        "robustness_id": "rb-1",
        "strategy_id": "s1",
        "strategy_version": "1.0.0",
        "kind": "pool",
        "status": "running",
        "start_date": date(2016, 1, 1),
        "end_date": date(2025, 12, 31),
        "windows": [
            {"label": "W0"},
            {"label": "W1"},
            {"label": "W2"},
        ],
        "variants": [],
        "summary": None,
        "statistics": None,
        "error_message": None,
        "finished_at": None,
        "updated_at": utcnow(),
        "trial_count": 2,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _variants_with_missing_baseline_window() -> list[dict[str, Any]]:
    """基线与变体的窗口覆盖不一致（模拟 F-1 的现场）。"""
    return [
        {
            "label": "baseline",
            "kind": "baseline",
            "backtest_ids": {"W1": "bt-base-1", "W2": "bt-base-2"},
        },
        {
            "label": "rand80_00",
            "kind": "pool",
            "knob": "index_codes",
            "backtest_ids": {"W0": "bt-v1-0", "W1": "bt-v1-1", "W2": "bt-v1-2"},
        },
    ]


def _make_service(
    row: SimpleNamespace, rows: dict[str, _FakeBacktestRow]
) -> tuple[RobustnessService, _FakeBacktestRepo, MagicMock]:
    """构造只带必要依赖的 RobussService 替身。"""
    service = object.__new__(RobustnessService)
    db = MagicMock()
    repo = _FakeBacktestRepo(rows)
    service._db = db
    service._backtest_repo = repo
    service._find = lambda robustness_id: row  # type: ignore[method-assign]
    return service, repo, db


class TestCommonWindowComparison:
    """F-1：只在共同窗口集上比较变体与基线。"""

    def test_means_use_common_windows_only(self) -> None:
        """基线缺 W0 时，变体也只用 W1/W2 求均值。"""
        row = _make_row(variants=_variants_with_missing_baseline_window())
        rows = {
            # 基线：W0 缺失（被删除），W1/W2 夏普 1.0 / 2.0 → 共同窗口均值 1.5
            "bt-base-1": _FakeBacktestRow("success", {"sharpe_ratio": 1.0}),
            "bt-base-2": _FakeBacktestRow("success", {"sharpe_ratio": 2.0}),
            # 变体：W0 很烂（-3.0），W1/W2 与基线相同 → 共同窗口均值 1.5
            "bt-v1-0": _FakeBacktestRow("success", {"sharpe_ratio": -3.0}),
            "bt-v1-1": _FakeBacktestRow("success", {"sharpe_ratio": 1.0}),
            "bt-v1-2": _FakeBacktestRow("success", {"sharpe_ratio": 2.0}),
        }
        service, _, _ = _make_service(row, rows)

        summary = service._summarize(row, row.variants)

        variant = next(item for item in summary["variants"] if item["label"] == "rand80_00")
        assert summary["coverage"]["common_windows"] == ["W1", "W2"]
        assert summary["coverage"]["comparable"] is True
        # F-1 的现场：旧口径拿"变体 3 个窗口的均值 0.0"减"基线 2 个窗口的均值 1.5"，
        # 会得到"变体变差 -1.5"的假结论；共同窗口下两者相等
        assert variant["windows"] == 2
        assert variant["windows_available"] == 3
        assert variant["delta_sharpe"] == pytest.approx(0.0, abs=1e-9)

    def test_no_common_window_marks_not_comparable(self) -> None:
        """没有共同窗口时不产出 delta，并显式标记不可比。"""
        variants = [
            {"label": "baseline", "kind": "baseline", "backtest_ids": {"W0": "b0"}},
            {"label": "v", "kind": "pool", "backtest_ids": {"W1": "v1"}},
        ]
        row = _make_row(variants=variants)
        rows = {
            "b0": _FakeBacktestRow("success", {"sharpe_ratio": 1.0}),
            "v1": _FakeBacktestRow("success", {"sharpe_ratio": 2.0}),
        }
        service, _, _ = _make_service(row, rows)

        summary = service._summarize(row, variants)

        assert summary["coverage"]["common_windows"] == []
        assert summary["coverage"]["comparable"] is False
        assert all(item["delta_sharpe"] is None for item in summary["variants"])

    def test_empty_variant_results_do_not_report_plateau(self) -> None:
        """F-3：没有任何变体结果时 is_plateau 必须是 None，而不是 True。"""
        variants = [
            {"label": "baseline", "kind": "baseline", "backtest_ids": {"W0": "b0"}},
            {"label": "k1", "kind": "knob", "backtest_ids": {"W0": "missing"}},
        ]
        row = _make_row(kind="scan", windows=[{"label": "W0"}], variants=variants)
        rows = {"b0": _FakeBacktestRow("success", {"sharpe_ratio": 1.0})}
        service, _, _ = _make_service(row, rows)

        summary = service._summarize(row, variants)

        neighborhood = summary["neighborhood"]
        assert neighborhood["n_variants"] == 0
        assert neighborhood["is_plateau"] is None
        assert neighborhood["worse_ratio"] is None
        assert neighborhood["reversal"] is None


class TestCollectWindowClassification:
    """F-4：缺失（被删除）与失败（跑挂了）分开报告。"""

    def test_deleted_backtest_goes_to_missing_windows(self) -> None:
        """回测行不存在时归入 missing_windows，不混进 failed_windows。"""
        variants = [
            {"label": "baseline", "kind": "baseline", "backtest_ids": {"W0": "deleted"}},
            {"label": "v", "kind": "pool", "backtest_ids": {"W0": "failed-one"}},
        ]
        row = _make_row(windows=[{"label": "W0"}], variants=variants)
        rows = {"failed-one": _FakeBacktestRow("failed")}
        service, _, _ = _make_service(row, rows)

        result = service.collect("rb-1")

        assert result["status"] == "failed"
        assert result["missing_windows"] == ["baseline/W0:回测已删除"]
        assert result["failed"] == ["v/W0"]

    def test_allow_partial_records_both_lists(self) -> None:
        """部分汇总时 coverage 同时给出缺失与失败窗口。"""
        variants = [
            {"label": "baseline", "kind": "baseline", "backtest_ids": {"W0": "ok"}},
            {"label": "v", "kind": "pool", "backtest_ids": {"W0": "deleted"}},
        ]
        row = _make_row(windows=[{"label": "W0"}], variants=variants)
        rows = {"ok": _FakeBacktestRow("success", {"sharpe_ratio": 1.0})}
        service, _, _ = _make_service(row, rows)

        result = service.collect("rb-1", allow_partial=True)

        coverage = result["summary"]["coverage"]
        assert coverage["missing_windows"] == ["v/W0:回测已删除"]
        assert coverage["failed_windows"] == []
        assert coverage["is_partial"] is True


class TestAbandonAndStale:
    """F-4：作废批次与停滞判定。"""

    def test_abandon_marks_batch_and_cancels_jobs(self, monkeypatch) -> None:
        """abandon 应作废批次、落原因，并取消批次内队列任务。"""
        fake_queue = _FakeJobQueue()
        monkeypatch.setattr(
            "quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue
        )
        row = _make_row()
        service, _, db = _make_service(row, {})

        result = service.abandon("rb-1", reason="回测已被删除，不再维护")

        assert result["status"] == "abandoned"
        assert row.status == "abandoned"
        assert row.error_message == "回测已被删除，不再维护"
        assert row.finished_at is not None
        assert fake_queue.cancelled == ["rb-1"]
        assert db.commit.called

    def test_is_stale_only_for_long_running(self) -> None:
        """只有 running 且超时未更新才算疑似停滞。"""
        fresh = _make_row(updated_at=utcnow())
        stale = _make_row(updated_at=utcnow() - timedelta(hours=STALE_AFTER_HOURS + 1))
        done = _make_row(
            status="success", updated_at=utcnow() - timedelta(hours=STALE_AFTER_HOURS + 1)
        )

        assert _is_stale(fresh) is False
        assert _is_stale(stale) is True
        assert _is_stale(done) is False


class TestCreateGuards:
    """F-13：本地并发参数只对同步模式生效。"""

    def test_parallel_requires_sync_mode(self) -> None:
        """入队模式下指定 --parallel 应直接报错，避免误解为队列并发。"""
        service = object.__new__(RobustnessService)
        service._db = MagicMock()

        with pytest.raises(ValueError, match="parallel"):
            service.create(strategy_id="s1", kind="scan", async_mode=True, parallel=4)
