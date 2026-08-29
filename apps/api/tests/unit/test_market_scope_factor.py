"""市场级因子（市场宽度）回测数据加载单元测试。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from quant_etf_api.engine.config import ScoreConfig, StrategyConfig
from quant_etf_api.services.backtest_service import BacktestService


def _make_service() -> BacktestService:
    """构建测试用 BacktestService（Mock 会话）。"""
    return BacktestService(db=MagicMock())


def _make_config() -> StrategyConfig:
    """构建引用市场宽度因子的策略配置。"""
    return StrategyConfig(
        strategy_id="s1",
        display_name="市场宽度策略",
        score=ScoreConfig(factors={"breadth_ma20_pct": 1.0}),
    )


class TestEnsureMarketScopeBars:
    """回测引用市场级因子时补充加载全市场行情。"""

    def test_loads_market_bars_when_market_scope_factor_used(self, monkeypatch) -> None:
        """策略引用 breadth_ma20_pct 时应加载全市场活跃指数行情。"""
        svc = _make_service()
        config = _make_config()
        trading_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        all_bars: dict = {("000300", date(2024, 1, 2)): object()}

        # Mock 活跃指数列表
        active_rows = [
            SimpleNamespace(index_code="000300"),
            SimpleNamespace(index_code="399673"),
            SimpleNamespace(index_code="931743"),
        ]
        fake_repo = MagicMock()
        fake_repo.find_active.return_value = active_rows
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.BenchmarkIndexRepository",
            lambda db: fake_repo,
        )

        # Mock 全市场行情加载
        extra_bars = {
            ("399673", date(2024, 1, 2)): object(),
            ("399673", date(2024, 1, 3)): object(),
            ("931743", date(2024, 1, 2)): object(),
            ("931743", date(2024, 1, 3)): object(),
        }
        svc._load_all_index_bars = MagicMock(return_value=extra_bars)

        svc._ensure_market_scope_bars(config, trading_dates, all_bars)

        # 全市场数据应并入 all_bars
        assert ("399673", date(2024, 1, 2)) in all_bars
        assert ("931743", date(2024, 1, 3)) in all_bars
        svc._load_all_index_bars.assert_called_once_with(
            trading_dates, ["399673", "931743"]
        )

    def test_noop_without_market_scope_factor(self) -> None:
        """策略未引用市场级因子时不应加载全市场行情。"""
        svc = _make_service()
        config = StrategyConfig(
            strategy_id="s1",
            display_name="普通策略",
            score=ScoreConfig(factors={"return_20d": 1.0}),
        )
        all_bars: dict = {("000300", date(2024, 1, 2)): object()}

        svc._load_all_index_bars = MagicMock(return_value={})
        svc._ensure_market_scope_bars(config, [date(2024, 1, 2)], all_bars)

        svc._load_all_index_bars.assert_not_called()
