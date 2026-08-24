"""调度器入队行为单元测试。

验证数据摄取与 AI 分析调度器只将任务入队（纯定时器），
不在调度线程内同步执行外部调用。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from quant_etf_api.infra.scheduler import AIAnalysisScheduler, DailyIngestScheduler


def _make_run_summary(run_id: str = "run-1") -> MagicMock:
    """构造最小运行记录摘要。"""
    summary = MagicMock()
    summary.run_id = run_id
    summary.trade_date = date.today()
    return summary


class TestDailyIngestScheduler:
    """日频摄取调度器测试。"""

    def test_execute_enqueues_daily_ingest(self, monkeypatch) -> None:
        """交易日应创建 run 并入队 daily_ingest，不执行摄取。"""
        fake_db = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db
        )
        fake_cal = MagicMock()
        fake_cal.is_trading_day.return_value = True
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.TradingCalendar", lambda: fake_cal
        )
        fake_run_svc = MagicMock()
        fake_run_svc.create_run.return_value = _make_run_summary()
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.RunService", lambda db: fake_run_svc
        )
        fake_queue = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.get_job_queue", lambda: fake_queue
        )
        scheduler = DailyIngestScheduler()

        scheduler._execute_daily_ingest()

        fake_run_svc.create_run.assert_called_once_with("daily_ingest", None, date.today())
        fake_queue.enqueue.assert_called_once_with(
            "daily_ingest",
            {"run_id": "run-1"},
            job_key="daily_ingest",
        )

    def test_non_trading_day_skips(self, monkeypatch) -> None:
        """非交易日应跳过，不创建 run 也不入队。"""
        fake_db = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db
        )
        fake_cal = MagicMock()
        fake_cal.is_trading_day.return_value = False
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.TradingCalendar", lambda: fake_cal
        )
        fake_run_svc = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.RunService", lambda db: fake_run_svc
        )
        fake_queue = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.get_job_queue", lambda: fake_queue
        )
        scheduler = DailyIngestScheduler()

        scheduler._execute_daily_ingest()

        fake_run_svc.create_run.assert_not_called()
        fake_queue.enqueue.assert_not_called()


class TestAIAnalysisScheduler:
    """AI 分析调度器测试。"""

    def test_execute_enqueues_ai_analysis(self, monkeypatch) -> None:
        """交易日应创建 run 并入队 ai_analysis，不执行分析。"""
        fake_db = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db
        )
        fake_cal = MagicMock()
        fake_cal.is_trading_day.return_value = True
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.TradingCalendar", lambda: fake_cal
        )
        fake_settings = MagicMock()
        fake_settings.ai_analysis_enabled = True
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.get_settings", lambda: fake_settings
        )
        fake_client = MagicMock()
        fake_client.validate.return_value = (True, None)
        monkeypatch.setattr(
            "quant_etf_api.infra.ai.client.AIClient",
            MagicMock(from_settings=lambda settings: fake_client),
        )
        fake_run_svc = MagicMock()
        fake_run_svc.create_run.return_value = _make_run_summary()
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.RunService", lambda db: fake_run_svc
        )
        fake_queue = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.scheduler.get_job_queue", lambda: fake_queue
        )
        scheduler = AIAnalysisScheduler()

        scheduler._execute_ai_analysis()

        fake_run_svc.create_run.assert_called_once_with("ai_analysis", None, date.today())
        fake_queue.enqueue.assert_called_once_with(
            "ai_analysis",
            {"run_id": "run-1"},
            job_key=f"ai_analysis:{date.today()}",
        )
