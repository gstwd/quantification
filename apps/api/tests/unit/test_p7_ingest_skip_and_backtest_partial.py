"""P7 修复测试：摄取 skipped 语义 + 回测分段提交的部分结果提示。

覆盖：
- daily_ingest / index_refresh / macro_refresh 在并发冲突时标记 run 为 skipped
- 非交易日触发 daily_ingest / index_refresh 时按"最近交易日缺口"补拉数据
- macro_refresh 仍保留非交易日跳过语义
- 回测中途失败时失败信息携带已保存的部分结果截止日期
- BacktestRepository.find_latest_daily_date 查询行为
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest

from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.models.core import ResearchRunModel
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.ingest_service import IngestService, _daily_ingest_lock
from quant_etf_api.services.strategy_config_service import StrategyConfigService


class _FakeCalendar:
    """TradingCalendar 替身：所有日期均判定为非交易日。"""

    def is_trading_day(self, day) -> bool:
        """返回 False，模拟非交易日。"""
        return False


class _FakeCalendarWithLatest:
    """TradingCalendar 替身：可配置最近交易日（模拟周末/节假日触发）。"""

    def __init__(self, latest: date) -> None:
        """初始化替身。

        Args:
            latest: latest_trading_day 返回的最近交易日。
        """
        self._latest = latest

    def latest_trading_day(self, reference: date | None = None) -> date:
        """返回配置的最近交易日。"""
        return self._latest


def _make_service(db) -> IngestService:
    """构造注入 mock db 的 IngestService。"""
    return IngestService(db=db)


def _make_catchup_service(
    latest_bar: date | None,
    latest_valuation: date | None,
    target_date: date,
) -> tuple[IngestService, mock.MagicMock]:
    """构造用于验证缺口补拉语义的 IngestService（仓库与上游均 mock）。

    Args:
        latest_bar: mock 仓库返回的指数最新日线日期。
        latest_valuation: mock 仓库返回的指数最新估值日期。
        target_date: 交易日历返回的最近交易日。

    Returns:
        (service, run_svc) 二元组，run_svc 用于断言状态流转。
    """
    db = mock.MagicMock()
    run_svc = mock.MagicMock()
    svc = IngestService(db=db, run_svc=run_svc)

    index = SimpleNamespace(index_code="000300")
    svc._index_repo = mock.MagicMock()
    svc._index_repo.find_all.return_value = [index]

    svc._index_bar_repo = mock.MagicMock()
    svc._index_bar_repo.get_latest_date.return_value = latest_bar
    svc._index_bar_repo.get_latest_trade_date.return_value = target_date

    svc._valuation_repo = mock.MagicMock()
    svc._valuation_repo.get_latest_date.return_value = latest_valuation

    svc._fetch_and_upsert_index_bars = mock.MagicMock(return_value=1)
    svc._fetch_and_upsert_index_valuation = mock.MagicMock(return_value=1)
    svc._fetch_and_upsert_macro = mock.MagicMock(return_value=0)
    svc._run_quality_checks = mock.MagicMock(return_value={})
    return svc, run_svc


class TestIngestSkippedSemantics:
    """摄取运行记录的 skipped 状态语义。"""

    def test_daily_ingest_concurrent_skip_marks_skipped(self) -> None:
        """并发冲突时 daily_ingest 标记为 skipped 而非 success。"""
        db = mock.MagicMock()
        assert _daily_ingest_lock.acquire(blocking=False)
        try:
            _make_service(db).run_daily_ingest("r1")
        finally:
            _daily_ingest_lock.release()

        # 状态流转走 RunService/ResearchRunRepository（db.get 返回运行行）
        run = db.get(ResearchRunModel, "r1")
        assert run.status == "skipped"
        assert run.metrics["reason"] == "concurrent_skip"
        db.commit.assert_called()

    @pytest.mark.parametrize("method", ["refresh_index_data", "refresh_macro_data"])
    def test_refresh_concurrent_skip_marks_skipped(self, method: str) -> None:
        """两个手动刷新入口在并发冲突时标记为 skipped。"""
        db = mock.MagicMock()
        assert _daily_ingest_lock.acquire(blocking=False)
        try:
            getattr(_make_service(db), method)("r1")
        finally:
            _daily_ingest_lock.release()

        run = db.get(ResearchRunModel, "r1")
        assert run.status == "skipped"
        assert run.metrics["reason"] == "concurrent_skip"

    def test_macro_refresh_holiday_marks_skipped(self) -> None:
        """macro_refresh 在非交易日时仍标记为 skipped（宏观数据不走补拉逻辑）。"""
        db = mock.MagicMock()
        with mock.patch(
            "quant_etf_api.services.ingest_service.TradingCalendar", _FakeCalendar
        ):
            _make_service(db).refresh_macro_data("r1")

        run = db.get(ResearchRunModel, "r1")
        assert run.status == "skipped"
        assert run.metrics["reason"] == "holiday"


class TestIngestCatchUpSemantics:
    """非交易日触发摄取时的"补到最近交易日"语义。"""

    @pytest.mark.parametrize("method", ["run_daily_ingest", "refresh_index_data"])
    def test_behind_latest_trading_day_fetches(self, method: str) -> None:
        """非交易日且数据落后于最近交易日时，应发起日线/估值补拉并标记 success。"""
        target = date(2026, 9, 4)
        svc, run_svc = _make_catchup_service(
            latest_bar=date(2026, 9, 3),
            latest_valuation=date(2026, 9, 3),
            target_date=target,
        )
        with mock.patch(
            "quant_etf_api.services.ingest_service.TradingCalendar",
            lambda: _FakeCalendarWithLatest(target),
        ):
            getattr(svc, method)("r1")

        svc._fetch_and_upsert_index_bars.assert_called_once_with("000300")
        svc._fetch_and_upsert_index_valuation.assert_called_once_with("000300")
        run_svc.mark_skipped.assert_not_called()
        assert run_svc.mark_success.call_count == 1

    @pytest.mark.parametrize("method", ["run_daily_ingest", "refresh_index_data"])
    def test_up_to_date_on_non_trading_day_skips_fetch(self, method: str) -> None:
        """非交易日但数据已覆盖最近交易日时，不发起外部拉取并标记 success。"""
        target = date(2026, 9, 4)
        svc, run_svc = _make_catchup_service(
            latest_bar=target,
            latest_valuation=target,
            target_date=target,
        )
        with mock.patch(
            "quant_etf_api.services.ingest_service.TradingCalendar",
            lambda: _FakeCalendarWithLatest(target),
        ):
            getattr(svc, method)("r1")

        svc._fetch_and_upsert_index_bars.assert_not_called()
        svc._fetch_and_upsert_index_valuation.assert_not_called()
        run_svc.mark_skipped.assert_not_called()
        assert run_svc.mark_success.call_count == 1

    def test_daily_ingest_returns_latest_bar_date(self) -> None:
        """daily_ingest 返回执行后的最新行情日期，供因子计算按实际日期入队。"""
        target = date(2026, 9, 4)
        svc, _ = _make_catchup_service(
            latest_bar=date(2026, 9, 3),
            latest_valuation=date(2026, 9, 3),
            target_date=target,
        )
        with mock.patch(
            "quant_etf_api.services.ingest_service.TradingCalendar",
            lambda: _FakeCalendarWithLatest(target),
        ):
            data_date = svc.run_daily_ingest("r1")

        assert data_date == target


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
