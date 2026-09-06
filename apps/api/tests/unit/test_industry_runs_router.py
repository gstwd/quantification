"""行业数据管理（P03）runs 触发端点回归测试。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from quant_etf_api.api.routers.runs import (
    industry_bars_refresh,
    industry_data_fill,
    industry_data_rebuild,
    industry_quality_check,
    industry_universe_refresh,
)
from quant_etf_api.schemas.run import ResearchRunSummary


def _make_summary(run_id: str = "run-ind-1") -> ResearchRunSummary:
    """构造真实的 ResearchRunSummary（不含 params 字段）。"""
    return ResearchRunSummary(
        run_id=run_id,
        run_type="industry_quality_check",
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


class TestIndustryRunEndpoints:
    """行业目录/日线刷新与单行业质量/补全/重拉端点。"""

    def test_universe_refresh_enqueue(self, monkeypatch) -> None:
        """refresh-info 端点入队 industry_universe_refresh 且带 job_key。"""
        fake_queue = _patch_deps(monkeypatch, _make_summary())

        result = industry_universe_refresh(db=MagicMock())

        assert result["status"] == "accepted"
        assert fake_queue.enqueue.call_args.args[0] == "industry_universe_refresh"
        assert fake_queue.enqueue.call_args.kwargs["job_key"] == "industry_universe_refresh"

    def test_bars_refresh_enqueue(self, monkeypatch) -> None:
        """refresh-bars 端点入队 industry_bars_refresh。"""
        fake_queue = _patch_deps(monkeypatch, _make_summary())

        result = industry_bars_refresh(db=MagicMock())

        assert result["run_id"] == "run-ind-1"
        assert fake_queue.enqueue.call_args.args[0] == "industry_bars_refresh"

    def test_single_industry_quality_enqueue(self, monkeypatch) -> None:
        """单行业质量检查入队 industry_quality_check 且带 industry_code。"""
        fake_queue = _patch_deps(monkeypatch, _make_summary())

        result = industry_quality_check("801010", db=MagicMock())

        assert result["status"] == "accepted"
        assert fake_queue.enqueue.call_args.args[1] == {
            "run_id": "run-ind-1",
            "industry_code": "801010",
        }
        assert fake_queue.enqueue.call_args.kwargs["job_key"] == "industry_quality:801010"

    def test_single_industry_fill_and_rebuild_enqueue(self, monkeypatch) -> None:
        """单行业补全/重拉端点分别入队对应任务类型。"""
        fake_queue = _patch_deps(monkeypatch, _make_summary())
        industry_data_fill("801010", db=MagicMock())
        assert fake_queue.enqueue.call_args.args[0] == "industry_data_fill"
        assert fake_queue.enqueue.call_args.kwargs["job_key"] == "industry_data_fill:801010"

        fake_queue.reset_mock()
        industry_data_rebuild("801010", db=MagicMock())
        assert fake_queue.enqueue.call_args.args[0] == "industry_data_rebuild"
        assert fake_queue.enqueue.call_args.kwargs["job_key"] == "industry_data_rebuild:801010"
