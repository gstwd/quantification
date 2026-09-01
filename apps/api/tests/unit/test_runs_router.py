"""runs 路由单指数任务的回归测试。

验证 rebuild / incremental-fill 端点不再访问 ResearchRunSummary 不存在的
params 字段（历史 bug：create_run 返回 Summary，但路由从 summary.params 取值
导致 AttributeError）。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from quant_etf_api.api.routers.runs import incremental_fill_index_data, rebuild_index_data
from quant_etf_api.schemas.run import ResearchRunSummary


def _make_summary(run_id: str = "run-1") -> ResearchRunSummary:
    """构造真实的 ResearchRunSummary（不含 params 字段，复现历史 bug 场景）。"""
    return ResearchRunSummary(
        run_id=run_id,
        run_type="index_rebuild",
        trade_date=date.today(),
        status="pending",
    )


def _patch_deps(monkeypatch, summary: ResearchRunSummary) -> MagicMock:
    """替换 RunService 与任务队列，返回假队列。"""
    fake_run_svc = MagicMock()
    fake_run_svc.create_run.return_value = summary
    monkeypatch.setattr(
        "quant_etf_api.api.routers.runs.RunService",
        lambda db: fake_run_svc,
    )
    fake_queue = MagicMock()
    monkeypatch.setattr(
        "quant_etf_api.api.routers.runs.get_job_queue",
        lambda: fake_queue,
    )
    return fake_queue


class TestSingleIndexRunEndpoints:
    """单指数 rebuild / incremental-fill 端点。"""

    def test_rebuild_passes_index_code(self, monkeypatch) -> None:
        """rebuild 端点将 index_code 传入入队参数，不访问 summary.params。"""
        fake_queue = _patch_deps(monkeypatch, _make_summary())

        result = rebuild_index_data("000300", db=MagicMock())

        assert result["status"] == "accepted"
        assert result["run_id"] == "run-1"
        assert fake_queue.enqueue.call_args.args[0] == "index_rebuild"
        assert fake_queue.enqueue.call_args.args[1] == {
            "run_id": "run-1",
            "index_code": "000300",
        }

    def test_incremental_fill_passes_index_code(self, monkeypatch) -> None:
        """incremental-fill 端点将 index_code 传入入队参数，不访问 summary.params。"""
        fake_queue = _patch_deps(monkeypatch, _make_summary())

        result = incremental_fill_index_data("399001", db=MagicMock())

        assert result["status"] == "accepted"
        assert result["run_id"] == "run-1"
        assert fake_queue.enqueue.call_args.args[0] == "index_incremental_fill"
        assert fake_queue.enqueue.call_args.args[1] == {
            "run_id": "run-1",
            "index_code": "399001",
        }
