"""对比回测 fan-in 单元测试。

验证对比任务处理器只入队两个子回测任务并立即返回，
子回测完成后通过 finalize_comparison_if_ready 汇总，
不再使用嵌套线程池。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from quant_etf_api.infra.job_queue.handlers import handle_backtest, handle_comparison
from quant_etf_api.services.backtest_service import BacktestService


def _make_backtest_row(status: str = "pending") -> SimpleNamespace:
    """构造最小回测行对象。"""
    return SimpleNamespace(
        status=status,
        error_message=None,
        metrics={
            "cumulative_return_pct": 10.0,
            "annualized_return_pct": 5.0,
            "max_drawdown_pct": -3.0,
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.5,
            "calmar_ratio": 1.1,
            "win_rate_pct": 60.0,
            "signal_accuracy_pct": 55.0,
            "total_trading_days": 100,
            "active_days": 80,
            "benchmark_return_pct": 8.0,
            "excess_return_pct": 2.0,
            "alpha": 0.1,
            "beta": 0.9,
            "information_ratio": 0.7,
        },
    )


class FakeComparisonRepo:
    """内存版对比回测仓库。"""

    def __init__(self, comp, bt_a=None, bt_b=None) -> None:
        self.comp = comp
        self.bt_map = {
            comp.backtest_a_id: bt_a or _make_backtest_row("success"),
            comp.backtest_b_id: bt_b or _make_backtest_row("success"),
        }
        self.calls: list[tuple[str, object]] = []

    def find_comparison_by_id(self, comparison_id: str):
        return self.comp

    def find_by_id(self, backtest_id: str):
        return self.bt_map.get(backtest_id)

    def mark_comparison_success(self, comparison_id: str, metrics: dict | None = None) -> None:
        self.calls.append(("success", metrics))

    def mark_comparison_failed(self, comparison_id: str, error_message: str) -> None:
        self.calls.append(("failed", error_message))

    def mark_comparison_partial(self, comparison_id: str, error_message: str) -> None:
        self.calls.append(("partial", error_message))


def _make_svc(repo: FakeComparisonRepo) -> BacktestService:
    """构建注入 fake 仓库的 BacktestService。"""
    svc = BacktestService(db=MagicMock())
    svc._backtest_repo = repo
    return svc


class TestFinalizeComparison:
    """finalize_comparison_if_ready 行为测试。"""

    def _comp(self) -> SimpleNamespace:
        return SimpleNamespace(
            comparison_id="comp-1",
            backtest_a_id="bt-a",
            backtest_b_id="bt-b",
        )

    def test_both_success_marks_success(self) -> None:
        """两个子回测均成功时汇总并标记成功。"""
        repo = FakeComparisonRepo(self._comp())
        svc = _make_svc(repo)

        svc.finalize_comparison_if_ready("comp-1")

        assert len(repo.calls) == 1
        call_type, metrics = repo.calls[0]
        assert call_type == "success"
        assert isinstance(metrics, dict)
        assert metrics["a_cumulative_return_pct"] == 10.0

    def test_one_failed_marks_partial(self) -> None:
        """一个子回测失败时标记 partial。"""
        comp = self._comp()
        repo = FakeComparisonRepo(
            comp,
            bt_a=_make_backtest_row("failed"),
        )
        svc = _make_svc(repo)

        svc.finalize_comparison_if_ready("comp-1")

        assert repo.calls[0][0] == "partial"

    def test_both_failed_marks_failed(self) -> None:
        """两个子回测均失败时标记 failed。"""
        comp = self._comp()
        repo = FakeComparisonRepo(
            comp,
            bt_a=_make_backtest_row("failed"),
            bt_b=_make_backtest_row("failed"),
        )
        svc = _make_svc(repo)

        svc.finalize_comparison_if_ready("comp-1")

        assert repo.calls[0][0] == "failed"

    def test_still_running_waits(self) -> None:
        """仍有子回测运行时不执行汇总。"""
        comp = self._comp()
        repo = FakeComparisonRepo(
            comp,
            bt_b=_make_backtest_row("running"),
        )
        svc = _make_svc(repo)

        svc.finalize_comparison_if_ready("comp-1")

        assert repo.calls == []

    def test_launch_comparison_marks_running(self) -> None:
        """launch_comparison 应标记 running 并返回两个子回测 ID。"""
        comp = self._comp()
        repo = FakeComparisonRepo(comp)
        svc = _make_svc(repo)

        children = svc.launch_comparison("comp-1")

        assert children == ("bt-a", "bt-b")
        assert comp.status == "running"
        assert comp.started_at is not None


class TestComparisonHandlers:
    """对比回测队列处理器测试。"""

    def test_handle_comparison_enqueues_two_backtests(self, monkeypatch) -> None:
        """comparison 处理器应入队两个 backtest 子任务并立即返回。"""
        fake_db = MagicMock()
        fake_svc = MagicMock()
        fake_svc.launch_comparison.return_value = ("bt-a", "bt-b")
        monkeypatch.setattr(
            "quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db
        )
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.BacktestService", lambda db: fake_svc
        )
        fake_queue = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue
        )

        handle_comparison({"comparison_id": "comp-1"})

        assert fake_svc.launch_comparison.call_count == 1
        assert fake_queue.enqueue.call_count == 2
        keys = [c.kwargs["job_key"] for c in fake_queue.enqueue.call_args_list]
        assert keys == ["comparison:comp-1:a", "comparison:comp-1:b"]
        payloads = [c.args[1] for c in fake_queue.enqueue.call_args_list]
        assert payloads[0]["backtest_id"] == "bt-a"
        assert payloads[0]["comparison_id"] == "comp-1"
        assert payloads[1]["backtest_id"] == "bt-b"

    def test_handle_backtest_triggers_finalize_with_comparison(self, monkeypatch) -> None:
        """带 comparison_id 的回测完成后应触发对比汇总。"""
        fake_db = MagicMock()
        fake_svc = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db
        )
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.BacktestService", lambda db: fake_svc
        )

        handle_backtest({"backtest_id": "bt-a", "comparison_id": "comp-1"})

        fake_svc.run_backtest.assert_called_once_with("bt-a")
        fake_svc.finalize_comparison_if_ready.assert_called_once_with("comp-1")

    def test_handle_backtest_without_comparison_skips_finalize(self, monkeypatch) -> None:
        """普通回测（无 comparison_id）不应触发对比汇总。"""
        fake_db = MagicMock()
        fake_svc = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db
        )
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.BacktestService", lambda db: fake_svc
        )

        handle_backtest({"backtest_id": "bt-a"})

        fake_svc.run_backtest.assert_called_once_with("bt-a")
        fake_svc.finalize_comparison_if_ready.assert_not_called()
