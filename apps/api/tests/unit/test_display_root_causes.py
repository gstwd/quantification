"""前端展示错误的后端根因回归测试。

覆盖：
1. 已退役策略的监控天数截止到退役日；
2. 生命周期复用研究期基线时按执行模型 / 记账口径匹配监控回测口径；
3. 子回测被取消时对比回测收口为失败，未开始即取消的回测同步落为 cancelled；
4. 回测列表摘要暴露执行模型与记账口径；
5. 行业日线给定日期区间时不按 limit 截断；
6. 重试不支持的运行类型不留下新运行记录，去重未入队时收口为跳过；
7. 被回收的运行类任务把对应运行记录标记为失败。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from quant_etf_api.api.routers import runs as runs_router
from quant_etf_api.api.routers.industry import industry_daily_bars
from quant_etf_api.domain.common.enums import DEFAULT_EXECUTION_MODEL
from quant_etf_api.domain.portfolio.execution import (
    ACCOUNTING_MODEL_DRIFT,
    ACCOUNTING_MODEL_LEGACY,
    EXECUTION_MODEL_CLOSE,
)
from quant_etf_api.infra.job_queue import handlers as job_handlers
from quant_etf_api.services import strategy_lifecycle_service as lifecycle_module
from quant_etf_api.services.backtest_comparison_service import BacktestComparisonService
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_lifecycle_service import StrategyLifecycleService


# ── 1. 监控天数 ─────────────────────────────────────────────────────────────


class TestMonitorDays:
    """上线监控天数口径。"""

    def test_retired_strategy_stops_at_retirement(self) -> None:
        """已退役：截止到退役日，不随当天增长。"""
        assert lifecycle_module._monitor_days(date(2026, 1, 1), date(2026, 9, 20)) == 262

    def test_live_strategy_counts_to_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """上线中：计到北京时间当天。"""
        monkeypatch.setattr(lifecycle_module, "today_cn", lambda: date(2026, 11, 5))
        assert lifecycle_module._monitor_days(date(2026, 1, 1), None) == 308

    def test_summary_uses_retirement_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """摘要（列表与详情共用）对退役策略返回截止退役日的天数。"""
        monkeypatch.setattr(lifecycle_module, "today_cn", lambda: date(2026, 11, 5))
        svc = StrategyLifecycleService.__new__(StrategyLifecycleService)
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.return_value = SimpleNamespace(display_name="示例")
        row = SimpleNamespace(
            strategy_id="s1",
            lifecycle_status="RETIRED",
            live_at=date(2026, 1, 1),
            retired_at=date(2026, 9, 20),
            latest_health_level="HEALTHY",
            latest_diagnosis="NORMAL",
            latest_recommended_action="KEEP",
            last_refreshed_at=None,
        )
        summary = svc._to_summary(row)  # type: ignore[arg-type]
        assert summary.live_days == 262


# ── 2. 基线口径匹配 ─────────────────────────────────────────────────────────


def _backtest_row(backtest_id: str, params: dict[str, Any] | None) -> SimpleNamespace:
    """最小回测行。"""
    return SimpleNamespace(backtest_id=backtest_id, params=params)


class TestReusableBaselineCaliber:
    """复用研究期基线时的执行模型 / 记账口径过滤。"""

    def _service(self, rows: list[SimpleNamespace]) -> StrategyLifecycleService:
        svc = StrategyLifecycleService.__new__(StrategyLifecycleService)
        svc._db = MagicMock()
        query = svc._db.query.return_value.filter.return_value.order_by.return_value
        query.all.return_value = rows
        return svc

    def _boundaries(self) -> SimpleNamespace:
        return SimpleNamespace(research_start=date(2016, 1, 1), research_end=date(2025, 12, 31))

    def test_skips_close_model_and_legacy_accounting(self) -> None:
        """收盘模型或历史记账的候选被跳过，取第一条同口径回测。"""
        svc = self._service(
            [
                _backtest_row(
                    "close",
                    {
                        "_execution_model": EXECUTION_MODEL_CLOSE,
                        "_accounting_model": ACCOUNTING_MODEL_DRIFT,
                    },
                ),
                _backtest_row("legacy", {"_execution_model": DEFAULT_EXECUTION_MODEL}),
                _backtest_row(
                    "match",
                    {
                        "_execution_model": DEFAULT_EXECUTION_MODEL,
                        "_accounting_model": ACCOUNTING_MODEL_DRIFT,
                    },
                ),
            ]
        )
        found = svc._find_reusable_research_backtest("s1", "hash", self._boundaries())  # type: ignore[arg-type]
        assert found == "match"

    def test_missing_execution_model_means_default(self) -> None:
        """缺执行模型键按系统缺省（开盘）解释。"""
        svc = self._service(
            [_backtest_row("implicit", {"_accounting_model": ACCOUNTING_MODEL_DRIFT})]
        )
        found = svc._find_reusable_research_backtest("s1", "hash", self._boundaries())  # type: ignore[arg-type]
        assert found == "implicit"

    def test_no_same_caliber_returns_none(self) -> None:
        """没有同口径回测时返回 None，由上线流程新建基线。"""
        svc = self._service(
            [
                _backtest_row(
                    "close",
                    {
                        "_execution_model": EXECUTION_MODEL_CLOSE,
                        "_accounting_model": ACCOUNTING_MODEL_DRIFT,
                    },
                )
            ]
        )
        assert svc._find_reusable_research_backtest("s1", "hash", self._boundaries()) is None  # type: ignore[arg-type]


# ── 3. 对比回测与取消 ───────────────────────────────────────────────────────


class _ComparisonRepo:
    """内存版对比仓库：记录收口调用。"""

    def __init__(self, status_a: str, status_b: str) -> None:
        self.comp = SimpleNamespace(backtest_a_id="a", backtest_b_id="b")
        self.rows = {
            "a": SimpleNamespace(status=status_a, error_message=None, metrics=None),
            "b": SimpleNamespace(status=status_b, error_message=None, metrics=None),
        }
        self.calls: list[tuple[str, str]] = []

    def find_comparison_by_id(self, comparison_id: str) -> SimpleNamespace:
        return self.comp

    def find_by_id(self, backtest_id: str) -> SimpleNamespace:
        return self.rows[backtest_id]

    def mark_comparison_failed(self, comparison_id: str, error_message: str) -> None:
        self.calls.append(("failed", error_message))

    def mark_comparison_partial(self, comparison_id: str, error_message: str) -> None:
        self.calls.append(("partial", error_message))


class TestComparisonCancelled:
    """子回测取消后的对比收口。"""

    def test_cancelled_side_finalizes_as_failed(self) -> None:
        """一侧取消、另一侧成功：对比判失败而不是一直 running。"""
        repo = _ComparisonRepo("cancelled", "success")
        svc = BacktestComparisonService(
            db=MagicMock(), backtest_service=MagicMock(), backtest_repo=repo
        )
        svc.finalize_comparison_if_ready("c1")
        assert repo.calls and repo.calls[0][0] == "failed"
        assert "子回测已取消" in repo.calls[0][1]

    def test_waits_while_other_side_running(self) -> None:
        """另一侧仍在执行时不收口。"""
        repo = _ComparisonRepo("cancelled", "running")
        svc = BacktestComparisonService(
            db=MagicMock(), backtest_service=MagicMock(), backtest_repo=repo
        )
        svc.finalize_comparison_if_ready("c1")
        assert repo.calls == []

    def test_pending_cancel_marks_backtest_and_finalizes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未开始即取消：回测落为 cancelled，并触发所属对比收口。"""
        svc = BacktestService.__new__(BacktestService)
        svc._db = MagicMock()
        svc._backtest_repo = MagicMock()
        svc._backtest_repo.find_by_id.return_value = SimpleNamespace(status="pending")
        queue = MagicMock()
        queue.find_snapshots_by_keys.side_effect = lambda keys: {
            keys[0]: SimpleNamespace(job_id="job-1", payload={"comparison_id": "cmp-1"})
        }
        queue.cancel_job.return_value = "cancelled"
        monkeypatch.setattr("quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: queue)
        finalized: list[str] = []
        monkeypatch.setattr(
            BacktestComparisonService,
            "finalize_comparison_if_ready",
            lambda self, comparison_id: finalized.append(comparison_id),
        )
        monkeypatch.setattr(BacktestComparisonService, "__init__", lambda self, db, **_: None)

        result = svc.cancel_backtest("bt-1")

        svc._backtest_repo.mark_cancelled.assert_called_once()
        assert finalized == ["cmp-1"]
        assert result["job_status"] == "cancelled"


# ── 4. 回测摘要口径字段 ─────────────────────────────────────────────────────


def _summary_row(status: str, params: dict[str, Any] | None) -> SimpleNamespace:
    """最小回测 ORM 行（_row_to_summary 所需字段）。"""
    return SimpleNamespace(
        backtest_id="bt",
        strategy_id="s1",
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        status=status,
        metrics=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=None,
        finished_at=None,
        error_message=None,
        progress=0,
        purpose="research",
        params=params,
        batch_id=None,
    )


class TestBacktestSummaryCaliber:
    """列表摘要的执行模型 / 记账口径。"""

    def _svc(self) -> BacktestService:
        return BacktestService.__new__(BacktestService)

    def test_reads_params(self) -> None:
        """有口径键时原样返回。"""
        summary = self._svc()._row_to_summary(
            _summary_row(
                "success",
                {
                    "_execution_model": EXECUTION_MODEL_CLOSE,
                    "_accounting_model": ACCOUNTING_MODEL_DRIFT,
                },
            )  # type: ignore[arg-type]
        )
        assert summary.execution_model == EXECUTION_MODEL_CLOSE
        assert summary.accounting_model == ACCOUNTING_MODEL_DRIFT

    def test_legacy_success_row(self) -> None:
        """缺键的已成功存量回测：开盘执行 + 历史记账。"""
        summary = self._svc()._row_to_summary(_summary_row("success", {}))  # type: ignore[arg-type]
        assert summary.execution_model == DEFAULT_EXECUTION_MODEL
        assert summary.accounting_model == ACCOUNTING_MODEL_LEGACY

    def test_pending_row_uses_current_accounting(self) -> None:
        """记账口径在执行时才写入：未产出结果的回测按当前口径，不误标为旧口径。"""
        summary = self._svc()._row_to_summary(
            _summary_row("pending", {"_execution_model": EXECUTION_MODEL_CLOSE})  # type: ignore[arg-type]
        )
        assert summary.accounting_model == ACCOUNTING_MODEL_DRIFT


# ── 5. 行业日线区间不截断 ───────────────────────────────────────────────────


def _bar(day: int) -> SimpleNamespace:
    """最小行业日线行。"""
    return SimpleNamespace(
        trade_date=date(2020, 1, 1).fromordinal(date(2020, 1, 1).toordinal() + day),
        industry_code="801010",
        open_price=1.0,
        high_price=1.0,
        low_price=1.0,
        close_price=1.0,
        prev_close_price=1.0,
        change_pct=0.0,
        volume=1.0,
        turnover=1.0,
        source="test",
        ingested_at=None,
    )


class TestIndustryBarsRange:
    """行业 K 线日期区间与 limit。"""

    def test_range_returns_all_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """给定区间时返回区间内全部日线，不按默认 limit=250 截断。"""
        repo = MagicMock()
        repo.find_range.return_value = [_bar(i) for i in range(700)]
        monkeypatch.setattr(
            "quant_etf_api.api.routers.industry.IndustryDailyBarRepository", lambda db: repo
        )
        rows = industry_daily_bars(
            "801010",
            start_date=date(2020, 1, 1),
            end_date=date(2023, 1, 1),
            limit=250,
            db=MagicMock(),
        )
        assert len(rows) == 700

    def test_limit_applies_without_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未给区间时仍按 limit 返回最近 N 行。"""
        repo = MagicMock()
        repo.find_by_code.return_value = [_bar(i) for i in range(700)]
        monkeypatch.setattr(
            "quant_etf_api.api.routers.industry.IndustryDailyBarRepository", lambda db: repo
        )
        rows = industry_daily_bars(
            "801010", start_date=None, end_date=None, limit=250, db=MagicMock()
        )
        assert len(rows) == 250
        assert rows[-1].trade_date == _bar(699).trade_date


# ── 6. 运行记录重试 ─────────────────────────────────────────────────────────


class _FakeRunService:
    """记录调用的运行服务替身。"""

    def __init__(self, detail: SimpleNamespace) -> None:
        self.detail = detail
        self.created: list[str] = []
        self.skipped: list[tuple[str, dict[str, Any] | None]] = []

    def get_run_detail(self, run_id: str) -> SimpleNamespace:
        return self.detail

    def create_run(
        self, run_type: str, strategy_id: str | None, trade_date: date, params: Any = None
    ) -> Any:
        self.created.append(run_type)
        return SimpleNamespace(run_id="new-run", trade_date=trade_date)

    def mark_skipped(self, run_id: str, metrics: dict[str, Any] | None = None) -> None:
        self.skipped.append((run_id, metrics))


class TestRetryRun:
    """重试运行记录的入队收口。"""

    def _detail(self, run_type: str) -> SimpleNamespace:
        return SimpleNamespace(
            run_type=run_type,
            status="failed",
            strategy_id=None,
            trade_date=date(2026, 11, 4),
            params=None,
        )

    def test_unsupported_type_creates_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """已下线类型：直接 400，不新建运行记录。"""
        fake = _FakeRunService(self._detail("daily_ingest"))
        monkeypatch.setattr(runs_router, "RunService", lambda db: fake)
        with pytest.raises(HTTPException) as exc_info:
            runs_router.retry_run("old-run", db=MagicMock())
        assert exc_info.value.status_code == 400
        assert fake.created == []

    def test_duplicate_job_marks_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同去重键已有活动任务：新运行记录收口为跳过，不留在 pending。"""
        fake = _FakeRunService(self._detail("data_sync_all"))
        monkeypatch.setattr(runs_router, "RunService", lambda db: fake)
        queue = MagicMock()
        queue.enqueue_with_status.return_value = ("existing-job", False)
        monkeypatch.setattr(runs_router, "get_job_queue", lambda: queue)
        result = runs_router.retry_run("old-run", db=MagicMock())
        assert result["status"] == "skipped"
        assert fake.skipped and fake.skipped[0][0] == "new-run"

    def test_created_job_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常新建任务：返回 accepted，不标记跳过。"""
        fake = _FakeRunService(self._detail("data_sync_all"))
        monkeypatch.setattr(runs_router, "RunService", lambda db: fake)
        queue = MagicMock()
        queue.enqueue_with_status.return_value = ("job-1", True)
        monkeypatch.setattr(runs_router, "get_job_queue", lambda: queue)
        result = runs_router.retry_run("old-run", db=MagicMock())
        assert result["status"] == "accepted"
        assert fake.skipped == []


# ── 7. 运行类任务回收 ───────────────────────────────────────────────────────


class TestAbandonRun:
    """被回收任务的运行记录收口。"""

    @pytest.mark.parametrize(
        "job_type", ["strategy_run", "data_sync_all", "data_manage_operation"]
    )
    def test_registered(self, job_type: str) -> None:
        """带 run_id 的任务类型都注册了回收回调。"""
        assert job_handlers.JOB_ABANDON_HANDLERS[job_type] is job_handlers.abandon_run

    @pytest.mark.parametrize(
        ("status", "expect_fail"), [("running", True), ("pending", True), ("success", False)]
    )
    def test_marks_only_active_runs_failed(
        self, monkeypatch: pytest.MonkeyPatch, status: str, expect_fail: bool
    ) -> None:
        """只有仍在 pending/running 的运行记录被标记失败，终态不改写。"""
        svc = MagicMock()
        svc.get_run_detail.return_value = SimpleNamespace(status=status)
        monkeypatch.setattr("quant_etf_api.services.run_service.RunService", lambda db: svc)
        monkeypatch.setattr("quant_etf_api.infra.db.base.SessionLocal", MagicMock)
        job_handlers.abandon_run({"run_id": "r1"}, "心跳超时")
        assert svc.mark_failed.called is expect_fail
