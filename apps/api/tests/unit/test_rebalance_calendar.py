"""回测调仓日历注入与口径指纹（C1）单元测试。

覆盖：
- 每日调仓（或未配置 rebalance）与日历无关，指纹记为 ``not_required``；
- 周度/月度调仓必须解析出真实日历，失败直接上抛（回测落 failed）；
- 使用本地日历表快照时产出 ``CALENDAR_SOURCE_DATABASE`` 信息级提示；
- ``DefaultRebalanceScheduler`` 日历必填，且不会把日历异常降级为"按星期比较"。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from quant_etf_api.domain.common.trading_calendar import (
    TradingCalendarUnavailableError,
)
from quant_etf_api.domain.strategies.rebalance import DefaultRebalanceScheduler
from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RebalanceConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService

DATES = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]


class _StubCalendar:
    """固定交易日集合的日历替身。"""

    def __init__(self, days: set[date]) -> None:
        self._days = days

    def is_trading_day(self, day: date) -> bool:
        """按给定集合判断。"""
        return day in self._days

    def latest_trading_day(self, day: date) -> date:
        """返回集合内不晚于 day 的最近日期。"""
        candidates = sorted(d for d in self._days if d <= day)
        return candidates[-1]


class _BrokenCalendar:
    """总是抛日历不可用异常的替身。"""

    def is_trading_day(self, day: date) -> bool:
        """始终抛错，模拟日历不可用。"""
        raise TradingCalendarUnavailableError("交易日历不可用：测试桩")

    def latest_trading_day(self, day: date) -> date:
        """始终抛错。"""
        raise TradingCalendarUnavailableError("交易日历不可用：测试桩")


def _config(frequency: str | None) -> StrategyConfig:
    """构造带可选调仓频率的最小策略配置。"""
    return StrategyConfig(
        strategy_id="t_cal",
        display_name="t_cal",
        index_codes=["000300"],
        score=ScoreConfig(factors={"return_5d": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight"),
        risk=RiskConfig(max_asset_weight=1.0),
        rebalance=RebalanceConfig(frequency=frequency, day_of_week=3) if frequency else None,
    )


def _row() -> BacktestRunModel:
    """构造内存回测行。"""
    return BacktestRunModel(
        backtest_id="bt-cal",
        strategy_id="t_cal",
        start_date=DATES[0],
        end_date=DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300"]},
        params={"_execution_model": "t_plus_1_open", "_data_quality_mode": "warn"},
        status="running",
    )


def _service() -> BacktestService:
    """构造仅依赖内存桩的 BacktestService。"""
    svc = BacktestService(db=MagicMock())
    svc._backtest_repo = MagicMock()
    return svc


class TestScheduleCalendarRequired:
    """调度器层面：日历必填、异常不降级。"""

    def test_calendar_is_required(self) -> None:
        """未注入交易日历时构造调度器直接报错。"""
        with pytest.raises(ValueError):
            DefaultRebalanceScheduler(None)  # type: ignore[arg-type]

    def test_calendar_error_is_not_downgraded(self) -> None:
        """日历抛错时 should_rebalance 上抛，而不是退化为按星期比较。"""
        scheduler = DefaultRebalanceScheduler(_BrokenCalendar())
        # 2025-01-08 是周三，目标 weekday=周二 → 进入窗口对齐逻辑并查询日历
        cfg = RebalanceConfig(frequency="weekly", day_of_week=1)
        with pytest.raises(TradingCalendarUnavailableError):
            scheduler.should_rebalance(cfg, date(2025, 1, 8))

    def test_weekly_alignment_uses_real_calendar(self) -> None:
        """周度调仓按真实交易日顺延（周三休市 → 顺延到下一个开市日）。"""
        # 2025-01-01 是周三（目标日）但被标记为休市，01-02 开市
        calendar = _StubCalendar({date(2025, 1, 2), date(2025, 1, 3)})
        scheduler = DefaultRebalanceScheduler(calendar)
        cfg = RebalanceConfig(frequency="weekly", day_of_week=2)
        assert scheduler.should_rebalance(cfg, date(2025, 1, 2)) is True
        assert scheduler.should_rebalance(cfg, date(2025, 1, 3)) is False


class TestResolveRebalanceCalendar:
    """回测侧解析与指纹。"""

    def test_daily_marks_not_required(self, monkeypatch) -> None:
        """每日调仓不解析日历，指纹为 not_required。"""
        svc = _service()
        called = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.resolve_trading_calendar", called
        )
        row = _row()
        source = svc._resolve_rebalance_calendar(row, _config("daily"))
        assert source == "not_required"
        assert row.params["_calendar_source"] == "not_required"
        assert svc._rebalance_scheduler is None
        called.assert_not_called()

    def test_no_rebalance_marks_not_required(self, monkeypatch) -> None:
        """未配置 rebalance 时同样不解析日历。"""
        svc = _service()
        called = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.resolve_trading_calendar", called
        )
        row = _row()
        assert svc._resolve_rebalance_calendar(row, _config(None)) == "not_required"
        called.assert_not_called()

    def test_weekly_injects_calendar_and_fingerprint(self, monkeypatch) -> None:
        """周度调仓注入日历并写入来源指纹。"""
        svc = _service()
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.resolve_trading_calendar",
            lambda db, required_range=None: (_StubCalendar(set(DATES)), "database"),
        )
        row = _row()
        source = svc._resolve_rebalance_calendar(row, _config("weekly"))
        assert source == "database"
        assert row.params["_calendar_source"] == "database"
        assert isinstance(svc._rebalance_scheduler, DefaultRebalanceScheduler)

    def test_weekly_fails_when_calendar_unavailable(self, monkeypatch) -> None:
        """周度调仓在日历不可用时直接上抛（不允许按星期近似）。"""

        def _raise(db: Any, required_range: Any = None) -> Any:
            raise TradingCalendarUnavailableError("交易日历不可用：测试")

        svc = _service()
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.resolve_trading_calendar", _raise
        )
        with pytest.raises(TradingCalendarUnavailableError):
            svc._resolve_rebalance_calendar(_row(), _config("monthly"))

    def test_check_rebalance_without_scheduler_raises(self) -> None:
        """未解析日历却走到调仓判断时显式报错（防止静默改为每日调仓）。"""
        svc = _service()
        with pytest.raises(ValueError):
            svc._check_rebalance(_config("weekly"), DATES[0], None)


def _stub_loop_service(calendar_source: str) -> tuple[BacktestService, dict[str, Any]]:
    """构造可在内存中跑完整主循环的服务（不连库）。"""
    svc = _service()
    svc._ensure_market_scope_bars = MagicMock()
    svc._get_lookback_days = MagicMock(return_value=90)
    svc._write_index_results = MagicMock(return_value=(0, 0))
    bars = {
        ("000300", d): SimpleNamespace(
            close_price=100.0 + i, open_price=100.0 + i, high_price=100.0 + i, low_price=100.0 + i
        )
        for i, d in enumerate(DATES)
    }
    universe = [{"index_code": "000300", "name_cn": "000300", "category": "broad_index"}]
    svc._prepare_backtest_data = MagicMock(
        return_value=(universe, ["000300"], list(DATES), bars, {}, {})
    )
    svc._factor_provider = MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = {
        d: {("000300", "return_5d"): 1.0} for d in DATES
    }
    svc._context_builder = MagicMock()
    svc._context_builder.build.side_effect = lambda config, trade_date, **kw: EngineContext(
        trade_date=trade_date,
        universe=[{"index_code": "000300", "name_cn": "000300", "category": "broad_index"}],
        asset_factors={},
    )
    svc._engine = MagicMock()
    svc._engine.run.side_effect = lambda config, context, include_details=False: EngineResult(
        trade_date=context.trade_date,
        strategy_id=config.strategy_id,
        timing=None,
        scores={"000300": 1.0},
        rankings=[],
        positions={"000300": 1.0},
        total_exposure=1.0,
        cash_ratio=0.0,
        strategy_results=[],
    )
    svc._backtest_repo = MagicMock()
    row = _row()
    row.params = dict(row.params or {}, _calendar_source=calendar_source)
    return svc, {"row": row}


class TestCalendarWarning:
    """口径提示。"""

    def test_database_source_emits_info_warning(self, monkeypatch) -> None:
        """使用本地日历快照时输出 CALENDAR_SOURCE_DATABASE 信息级提示。"""
        svc, ctx = _stub_loop_service("database")
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.resolve_trading_calendar",
            lambda db, required_range=None: (_StubCalendar(set(DATES)), "database"),
        )
        svc._run_backtest_loop("bt-cal", ctx["row"], _config("weekly"))
        codes = [w["code"] for w in svc._backtest_repo.mark_success.call_args.kwargs["warnings"]]
        assert "CALENDAR_SOURCE_DATABASE" in codes

    def test_upstream_source_has_no_warning(self, monkeypatch) -> None:
        """上游日历不产出降级提示。"""
        svc, ctx = _stub_loop_service("upstream")
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.resolve_trading_calendar",
            lambda db, required_range=None: (_StubCalendar(set(DATES)), "upstream"),
        )
        svc._run_backtest_loop("bt-cal", ctx["row"], _config("weekly"))
        codes = [w["code"] for w in svc._backtest_repo.mark_success.call_args.kwargs["warnings"]]
        assert "CALENDAR_SOURCE_DATABASE" not in codes
