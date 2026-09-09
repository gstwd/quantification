"""行业因子后台任务的落库门控与复合因子依赖测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quant_etf_api.infra.job_queue import handlers


def _patch_handler_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    metrics: dict[str, int],
) -> tuple[MagicMock, MagicMock]:
    """替换行业因子任务的数据库、运行记录与队列依赖。

    Args:
        monkeypatch: pytest monkeypatch 工具。
        metrics: 行业因子计算返回的统计指标。

    Returns:
        (队列模拟对象, 运行服务模拟对象)。
    """
    fake_db = MagicMock()
    fake_queue = MagicMock()
    fake_run_service = MagicMock()
    fake_run_service.create_run.return_value = SimpleNamespace(run_id="industry-run")
    monkeypatch.setattr("quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db)
    monkeypatch.setattr("quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue)
    monkeypatch.setattr("quant_etf_api.services.run_service.RunService", lambda _db: fake_run_service)
    monkeypatch.setattr(
        "quant_etf_api.services.industry_factor_service.IndustryFactorService.compute_and_store",
        lambda _self, **_kwargs: metrics,
    )
    return fake_queue, fake_run_service


def test_industry_factor_completion_requeues_default_composite_factor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认行业面板成功落库后，应入队同日指数复合因子重算。"""
    fake_queue, fake_run_service = _patch_handler_dependencies(
        monkeypatch,
        {"industry_count": 31, "date_count": 1, "factor_row_count": 124},
    )

    handlers.handle_industry_factor_compute({"trade_date": "2026-09-08"})

    fake_run_service.mark_success.assert_called_once()
    fake_queue.enqueue.assert_called_once_with(
        "factor_computation",
        {"trade_date": "2026-09-08"},
        job_key="factor_computation:2026-09-08",
    )


def test_industry_factor_zero_rows_marks_run_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """零行落库不是成功结果，应失败并禁止触发空复合因子重算。"""
    fake_queue, fake_run_service = _patch_handler_dependencies(
        monkeypatch,
        {"industry_count": 31, "date_count": 1, "factor_row_count": 0},
    )

    with pytest.raises(RuntimeError, match="未写入任何行"):
        handlers.handle_industry_factor_compute({"trade_date": "2026-09-08"})

    fake_run_service.mark_success.assert_not_called()
    fake_run_service.mark_failed.assert_called_once()
    fake_queue.enqueue.assert_not_called()
