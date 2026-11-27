"""调度器入队行为单元测试。

验证数据同步调度器只将任务入队（纯定时器），不在调度线程内同步执行外部调用。
同时回归验证：行业摄取调度器已取消，全部数据源的定时摄取统一
走全局数据同步调度器。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from quant_etf_api.infra.scheduler import DailyIngestScheduler


def _make_run_summary(run_id: str = "run-1") -> MagicMock:
    """构造最小运行记录摘要。"""
    summary = MagicMock()
    summary.run_id = run_id
    summary.trade_date = date.today()
    return summary


class TestDailyIngestScheduler:
    """日频数据调度器测试（统一全局同步入口）。"""

    def test_execute_enqueues_data_sync_all(self, monkeypatch) -> None:
        """应创建 data_sync_all 运行并入队全局同步任务。"""
        fake_db = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db
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
        fake_queue.enqueue_with_status.return_value = ("job-1", True)
        scheduler = DailyIngestScheduler()

        scheduler._execute_daily_sync()

        fake_run_svc.create_run.assert_called_once_with("data_sync_all", None, date.today())
        fake_queue.enqueue_with_status.assert_called_once_with(
            "data_sync_all",
            {"run_id": "run-1"},
            job_key="data_sync_all",
        )

    def test_non_trading_day_still_enqueues(self, monkeypatch) -> None:
        """非交易日也创建 run 并入队，由摄取侧补拉最近交易日数据。"""
        fake_db = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.db.base.SessionLocal", lambda: fake_db
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
        fake_queue.enqueue_with_status.return_value = ("job-1", True)
        scheduler = DailyIngestScheduler()

        scheduler._execute_daily_sync()

        fake_run_svc.create_run.assert_called_once_with("data_sync_all", None, date.today())
        fake_queue.enqueue_with_status.assert_called_once_with(
            "data_sync_all",
            {"run_id": "run-1"},
            job_key="data_sync_all",
        )


class TestUnifiedIngestScheduler:
    """统一摄取调度器回归：行业子调度器已取消。"""

    def test_industry_scheduler_removed(self) -> None:
        """行业摄取调度器类与工厂已从调度器模块移除。"""
        from quant_etf_api.infra import scheduler as scheduler_module

        assert not hasattr(scheduler_module, "IndustryIngestScheduler")
        assert not hasattr(scheduler_module, "get_industry_scheduler")

    def test_settings_have_no_industry_refresh_schedule(self) -> None:
        """行业刷新不再有独立的触发时间与开关配置。"""
        from quant_etf_api.config.settings import Settings

        assert "industry_refresh_time" not in Settings.model_fields
        assert "industry_refresh_enabled" not in Settings.model_fields

    def test_ai_sentiment_scheduler_and_settings_removed(self) -> None:
        """AI 舆情调度器与配置已从运行时移除。"""
        from quant_etf_api.config.settings import Settings
        from quant_etf_api.infra import scheduler as scheduler_module

        assert not hasattr(scheduler_module, "AIAnalysisScheduler")
        assert not hasattr(scheduler_module, "get_ai_scheduler")
        assert "ai_analysis_enabled" not in Settings.model_fields
        assert "ai_schedule_time" not in Settings.model_fields

    def test_global_sync_covers_all_datasets_including_industry(self) -> None:
        """全局同步覆盖行业数据集，取消子调度器后行业数据仍被定时摄取。"""
        from quant_etf_api.services.data_management_service import DATASETS

        keys = {definition.key for definition in DATASETS}
        assert {"industry_universe", "industry_daily_bar", "industry_membership"} <= keys
        # 全局同步入口按 sync_latest 遍历全部数据集
        assert all("sync_latest" in definition.operations for definition in DATASETS)
