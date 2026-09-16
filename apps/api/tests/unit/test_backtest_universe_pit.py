"""回测标的池 point-in-time 口径单元测试（消除幸存者偏差）。

覆盖：
- ``BenchmarkIndexRepository.find_for_period`` 的三段过滤（活跃 / 退市日未知 /
  退市日晚于区间起点）；
- ``_resolve_index_universe`` 改用区间起点解析，并给 universe 项打上 is_active；
- 主循环对"标的池包含已停用指数"产出 UNIVERSE_INCLUDES_DELISTED 提示。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import PortfolioConfig, RankConfig, ScoreConfig, StrategyConfig
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.services.backtest_service import BacktestService


class TestFindForPeriod:
    """仓库层：point-in-time 过滤条件。"""

    def test_filter_has_three_point_in_time_branches(self) -> None:
        """过滤条件是"活跃 OR 退市日未知 OR 退市日不早于区间起点"三段式。"""
        db = MagicMock()
        BenchmarkIndexRepository(db).find_for_period(date(2016, 1, 1))

        expr = db.query.return_value.filter.call_args.args[0]
        assert len(expr.clauses) == 3
        rendered = str(expr)
        assert "is_active" in rendered
        assert "delisting_date" in rendered


class TestResolveIndexUniverse:
    """服务层：区间起点解析 + is_active 标记。"""

    def test_uses_period_scope_and_marks_active(self, monkeypatch) -> None:
        """停用但区间内仍存续的指数必须纳入，并标记为未活跃。"""
        svc = BacktestService(db=MagicMock())
        rows = [
            SimpleNamespace(index_code="000300", name_cn="沪深300", is_active=True),
            SimpleNamespace(index_code="399999", name_cn="退市指数", is_active=False),
        ]
        fake_repo = MagicMock()
        fake_repo.find_for_period.return_value = rows
        fake_repo.find_active.return_value = [rows[0]]
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.BenchmarkIndexRepository",
            lambda db: fake_repo,
        )

        items = svc._resolve_index_universe({"mode": "all"}, date(2016, 1, 1))

        fake_repo.find_for_period.assert_called_once_with(date(2016, 1, 1))
        assert [item["index_code"] for item in items] == ["000300", "399999"]
        assert items[0]["is_active"] is True
        assert items[1]["is_active"] is False

    def test_subset_filter_applies_on_period_scope(self, monkeypatch) -> None:
        """subset 模式在 point-in-time 集合内再按代码过滤。"""
        svc = BacktestService(db=MagicMock())
        rows = [
            SimpleNamespace(index_code="000300", name_cn="沪深300", is_active=True),
            SimpleNamespace(index_code="399999", name_cn="退市指数", is_active=False),
        ]
        fake_repo = MagicMock()
        fake_repo.find_for_period.return_value = rows
        fake_repo.find_active.return_value = [rows[0]]
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.BenchmarkIndexRepository",
            lambda db: fake_repo,
        )

        items = svc._resolve_index_universe(
            {"mode": "subset", "index_codes": ["399999"]}, date(2016, 1, 1)
        )

        assert [item["index_code"] for item in items] == ["399999"]
        assert items[0]["is_active"] is False


def _make_config() -> StrategyConfig:
    """构造最小可运行策略配置（不限定 index_codes，便于验证标的池口径）。"""
    return StrategyConfig(
        strategy_id="t_pit",
        display_name="t_pit",
        score=ScoreConfig(factors={"return_5d": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight", default_exposure=1.0),
    )


def _stub_loop_service() -> tuple[BacktestService, dict[str, Any]]:
    """构造可在内存中跑完整主循环的服务（标的池含一个已停用指数）。"""
    dates = [date(2025, 1, 2), date(2025, 1, 3)]
    svc = BacktestService(db=MagicMock())
    svc._ensure_market_scope_bars = MagicMock()
    svc._get_lookback_days = MagicMock(return_value=90)
    svc._write_index_results = MagicMock(return_value=(0, 0))

    bars = {
        (code, d): SimpleNamespace(
            close_price=100.0, open_price=100.0, high_price=100.0, low_price=100.0
        )
        for code in ("000300", "399999")
        for d in dates
    }
    universe = [
        {
            "index_code": "000300",
            "name_cn": "000300",
            "category": "broad_index",
            "is_active": True,
        },
        {
            "index_code": "399999",
            "name_cn": "399999",
            "category": "broad_index",
            "is_active": False,
        },
    ]
    svc._prepare_backtest_data = MagicMock(
        return_value=(universe, ["000300", "399999"], list(dates), bars, {}, {})
    )
    svc._factor_provider = MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = {
        d: {("000300", "return_5d"): 1.0, ("399999", "return_5d"): 1.0} for d in dates
    }
    svc._context_builder = MagicMock()
    svc._context_builder.build.side_effect = lambda config, trade_date, **kw: EngineContext(
        trade_date=trade_date,
        universe=[
            {"index_code": c, "name_cn": c, "category": "broad_index"}
            for c in (kw.get("index_codes") or [])
        ],
        asset_factors={},
    )
    svc._engine = MagicMock()
    svc._engine.run.side_effect = lambda config, context, include_details=False: EngineResult(
        trade_date=context.trade_date,
        strategy_id=config.strategy_id,
        timing=None,
        scores={},
        rankings=[],
        positions={},
        total_exposure=0.0,
        cash_ratio=1.0,
        strategy_results=[],
    )
    svc._backtest_repo = MagicMock()
    svc._backtest_repo.add_daily_result.side_effect = lambda row: None
    svc._backtest_repo.add_index_result.side_effect = lambda row: None

    row = BacktestRunModel(
        backtest_id="bt-pit",
        strategy_id="t_pit",
        start_date=dates[0],
        end_date=dates[-1],
        universe_filter={"mode": "all"},
        params={
            "_execution_model": "t_plus_1_open",
            "_data_quality_mode": "warn",
            "_enable_benchmark": False,
        },
        status="running",
    )
    return svc, {"row": row}


class TestDelistedUniverseWarning:
    """主循环口径提示。"""

    def test_emits_info_warning_when_universe_has_delisted(self) -> None:
        """标的池含已停用指数时输出 UNIVERSE_INCLUDES_DELISTED 提示。"""
        svc, ctx = _stub_loop_service()
        svc._run_backtest_loop("bt-pit", ctx["row"], _make_config())

        warnings = svc._backtest_repo.mark_success.call_args.kwargs["warnings"]
        matched = [w for w in warnings if w["code"] == "UNIVERSE_INCLUDES_DELISTED"]
        assert len(matched) == 1
        assert matched[0]["level"] == "info"
        assert "399999" in matched[0]["message"]
