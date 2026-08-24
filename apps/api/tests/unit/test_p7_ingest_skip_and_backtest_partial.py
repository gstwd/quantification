"""P7 修复测试：摄取 skipped 语义 + 回测分段提交的部分结果提示。

覆盖：
- daily_ingest / 三个手动刷新入口在并发冲突与非交易日时标记 run 为 skipped
- 回测中途失败时失败信息携带已保存的部分结果截止日期
- BacktestRepository.find_latest_daily_date 查询行为
"""

from __future__ import annotations

from datetime import date
from unittest import mock

import pytest

from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.ingest_service import IngestService, _daily_ingest_lock
from quant_etf_api.services.strategy_config_service import StrategyConfigService


class _FakeCalendar:
    """TradingCalendar 替身：所有日期均判定为非交易日。"""

    def is_trading_day(self, day) -> bool:
        """返回 False，模拟非交易日。"""
        return False


def _make_service(db) -> IngestService:
    """构造注入 mock db 的 IngestService。"""
    return IngestService(db=db)


class TestIngestSkippedSemantics:
    """摄取运行记录的 skipped 状态语义。"""

    def test_daily_ingest_concurrent_skip_marks_skipped(self) -> None:
        """并发冲突时 daily_ingest 标记为 skipped 而非 success。"""
        db = mock.MagicMock()
        mock_run = db.query.return_value.filter.return_value.first.return_value
        assert _daily_ingest_lock.acquire(blocking=False)
        try:
            _make_service(db).run_daily_ingest("r1")
        finally:
            _daily_ingest_lock.release()

        assert mock_run.status == "skipped"
        assert mock_run.metrics["reason"] == "concurrent_skip"
        db.commit.assert_called()

    def test_daily_ingest_holiday_marks_skipped(self) -> None:
        """非交易日时 daily_ingest 标记为 skipped。"""
        db = mock.MagicMock()
        mock_run = db.query.return_value.filter.return_value.first.return_value
        with mock.patch(
            "quant_etf_api.services.ingest_service.TradingCalendar", _FakeCalendar
        ):
            _make_service(db).run_daily_ingest("r1")

        assert mock_run.status == "skipped"
        assert mock_run.metrics["reason"] == "holiday"

    @pytest.mark.parametrize(
        "method",
        ["refresh_etf_data", "refresh_index_data", "refresh_macro_data"],
    )
    def test_refresh_concurrent_skip_marks_skipped(self, method: str) -> None:
        """三个手动刷新入口在并发冲突时标记为 skipped。"""
        db = mock.MagicMock()
        mock_run = db.query.return_value.filter.return_value.first.return_value
        assert _daily_ingest_lock.acquire(blocking=False)
        try:
            getattr(_make_service(db), method)("r1")
        finally:
            _daily_ingest_lock.release()

        assert mock_run.status == "skipped"
        assert mock_run.metrics["reason"] == "concurrent_skip"

    @pytest.mark.parametrize(
        "method",
        ["refresh_etf_data", "refresh_index_data", "refresh_macro_data"],
    )
    def test_refresh_holiday_marks_skipped(self, method: str) -> None:
        """三个手动刷新入口在非交易日时标记为 skipped。"""
        db = mock.MagicMock()
        mock_run = db.query.return_value.filter.return_value.first.return_value
        with mock.patch(
            "quant_etf_api.services.ingest_service.TradingCalendar", _FakeCalendar
        ):
            getattr(_make_service(db), method)("r1")

        assert mock_run.status == "skipped"
        assert mock_run.metrics["reason"] == "holiday"


class TestBacktestPartialResults:
    """回测分段提交后的失败信息与部分结果查询。"""

    def _make_config(self) -> StrategyConfig:
        """构造含 portfolio 的合法解析配置。"""
        return StrategyConfig(
            strategy_id="s1",
            display_name="测试",
            score=ScoreConfig(factors={"return_20d": 1.0}),
            rank=RankConfig(),
            portfolio=PortfolioConfig(method="equal_weight"),
        )

    def test_run_backtest_failure_includes_partial_date(self) -> None:
        """回测中途失败时，失败信息携带已保存的部分结果截止日期。"""
        db = mock.MagicMock()
        repo = mock.MagicMock()
        repo.find_by_id.return_value = mock.MagicMock()
        repo.find_latest_daily_date.return_value = date(2024, 6, 30)
        svc = BacktestService(db=db, backtest_repo=repo)

        with (
            mock.patch.object(
                StrategyConfigService, "get_parsed_config", return_value=self._make_config()
            ),
            mock.patch.object(
                BacktestService, "_run_backtest_loop", side_effect=ValueError("boom")
            ),
        ):
            svc.run_backtest("bt1")

        repo.mark_failed.assert_called_once()
        message = repo.mark_failed.call_args[0][1]
        assert "boom" in message
        assert "2024-06-30" in message
        assert "已保存部分结果" in message

    def test_run_backtest_failure_without_partial_data(self) -> None:
        """无已提交部分结果时，失败信息不拼接部分结果提示。"""
        db = mock.MagicMock()
        repo = mock.MagicMock()
        repo.find_by_id.return_value = mock.MagicMock()
        repo.find_latest_daily_date.return_value = None
        svc = BacktestService(db=db, backtest_repo=repo)

        with (
            mock.patch.object(
                StrategyConfigService, "get_parsed_config", return_value=self._make_config()
            ),
            mock.patch.object(
                BacktestService, "_run_backtest_loop", side_effect=ValueError("boom")
            ),
        ):
            svc.run_backtest("bt1")

        repo.mark_failed.assert_called_once()
        message = repo.mark_failed.call_args[0][1]
        assert "已保存部分结果" not in message

    def test_find_latest_daily_date_returns_max_date(self) -> None:
        """仓库查询返回已保存的最新每日结果日期。"""
        db = mock.MagicMock()
        repo = BacktestRepository(db=db)
        db.query.return_value.filter.return_value.scalar.return_value = date(2024, 6, 30)

        assert repo.find_latest_daily_date("bt1") == date(2024, 6, 30)
