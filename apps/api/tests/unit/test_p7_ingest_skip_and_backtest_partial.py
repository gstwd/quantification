"""P7 修复测试：回测分段提交的部分结果提示。

覆盖：
- 回测中途失败时失败信息携带已保存的部分结果截止日期
- BacktestRepository.find_latest_daily_date 查询行为

说明：本文件原含 daily_ingest / index_refresh / macro_refresh 的并发
skipped 语义与"补到最近交易日"用例。这些摄取入口已随数据管理统一编排
下线（定时与手动同步、缺口修复、全量重拉统一由 DataManagementService
承担），对应用例一并移除。
"""

from __future__ import annotations

from datetime import date
from unittest import mock

from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import StrategyConfigService


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
