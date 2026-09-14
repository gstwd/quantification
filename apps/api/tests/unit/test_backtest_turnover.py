"""换手率计入清仓/建仓腿（C2）单元测试。

历史口径在 ``prev`` 或 ``new`` 为空时把换手记为 0，导致清仓型调仓腿的成本
被系统性低估；现在统一按 ``Σ|Δw|/2`` 计入，并用 ``_turnover_model`` 指纹区分
存量回测（legacy_v1）与新回测（delta_w_v2）。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from quant_etf_api.domain.portfolio.turnover import (
    TURNOVER_MODEL_DELTA_W,
    TURNOVER_MODEL_LEGACY,
    compute_turnover,
)
from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.services.backtest_service import BacktestService

DATES = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]


def _config() -> StrategyConfig:
    """构造每日调仓的最小配置。"""
    return StrategyConfig(
        strategy_id="t_turnover",
        display_name="t_turnover",
        index_codes=["000300"],
        score=ScoreConfig(factors={"return_5d": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight"),
        risk=RiskConfig(max_asset_weight=1.0),
    )


class TestComputeTurnoverLegs:
    """领域函数本身覆盖清仓/建仓腿。"""

    def test_open_from_cash(self) -> None:
        """空仓建仓：换手 = 新仓位绝对值之和 / 2。"""
        assert compute_turnover({}, {"a": 1.0}) == 0.5

    def test_liquidate_to_cash(self) -> None:
        """清仓：换手 = 旧仓位绝对值之和 / 2。"""
        assert compute_turnover({"a": 1.0}, {}) == 0.5

    def test_full_switch(self) -> None:
        """换仓：两侧腿都计入。"""
        assert compute_turnover({"a": 0.5}, {"b": 0.5}) == 0.5


def _stub_loop_service(positions_by_index: list[dict[str, float]]) -> tuple[BacktestService, Any]:
    """构造逐日返回指定持仓的内存回测服务。"""
    svc = BacktestService(db=MagicMock())
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
        universe=list(universe),
        asset_factors={},
    )
    call_index = {"i": 0}

    def _run_engine(
        config: StrategyConfig, context: EngineContext, include_details: bool = False
    ) -> EngineResult:
        """按预设序列逐日返回持仓。"""
        idx = min(call_index["i"], len(positions_by_index) - 1)
        call_index["i"] += 1
        positions = positions_by_index[idx]
        return EngineResult(
            trade_date=context.trade_date,
            strategy_id=config.strategy_id,
            timing=None,
            scores={"000300": 1.0},
            rankings=[],
            positions=positions,
            total_exposure=sum(positions.values()),
            cash_ratio=1.0 - sum(positions.values()),
            strategy_results=[],
        )

    svc._engine = MagicMock()
    svc._engine.run.side_effect = _run_engine
    collected: list[Any] = []
    svc._backtest_repo = MagicMock()
    svc._backtest_repo.add_daily_result.side_effect = collected.append
    row = BacktestRunModel(
        backtest_id="bt-turnover",
        strategy_id="t_turnover",
        start_date=DATES[0],
        end_date=DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300"]},
        params={"_execution_model": "t_plus_1_open", "_data_quality_mode": "warn"},
        status="running",
    )
    return svc, {"row": row, "collected": collected}


class TestTurnoverInLoop:
    """主循环落库的换手值。"""

    def test_liquidation_leg_records_turnover(self) -> None:
        """建仓日与清仓日的换手都必须落库（旧口径会把清仓日记为 0/None）。"""
        svc, ctx = _stub_loop_service([{"000300": 1.0}, {}, {}])
        svc._run_backtest_loop("bt-turnover", ctx["row"], _config())
        rows = ctx["collected"]
        assert len(rows) == len(DATES)
        # 第 1 日：空仓 → 满仓（建仓腿）
        assert rows[0].turnover == 0.5
        # 第 2 日：满仓 → 空仓（清仓腿，旧口径为 None）
        assert rows[1].turnover == 0.5
        # 第 3 日：仍旧空仓 → 无换手
        assert rows[2].turnover is None


class TestTurnoverModelFingerprint:
    """换手口径指纹。"""

    def test_new_backtest_writes_delta_w_model(self, monkeypatch) -> None:
        """新建回测写入 delta_w_v2 指纹。"""

        class _FakeConfigService:
            def __init__(self, db: object) -> None:
                self._db = db

            def get_parsed_config(self, strategy_id: str) -> StrategyConfig:
                return _config()

            def validate_parsed(self, config: StrategyConfig) -> object:
                return type("R", (), {"valid": True, "errors": []})()

            def get_config(self, strategy_id: str) -> None:
                return None

        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.StrategyConfigService",
            _FakeConfigService,
        )
        db = MagicMock()
        svc = BacktestService(db=db)
        svc.create_backtest(
            BacktestCreateRequest(
                strategy_id="t_turnover",
                start_date=DATES[0],
                end_date=DATES[-1],
            )
        )
        row = db.add.call_args.args[0]
        assert row.params["_turnover_model"] == TURNOVER_MODEL_DELTA_W

    def test_legacy_rows_read_as_legacy_model(self) -> None:
        """缺少指纹的存量回测按 legacy_v1 标注。"""
        svc = BacktestService(db=MagicMock())
        row = BacktestRunModel(
            backtest_id="bt-legacy",
            strategy_id="t_turnover",
            start_date=DATES[0],
            end_date=DATES[-1],
            universe_filter={"mode": "all"},
            params={"_cost_bps": 10.0, "_enable_benchmark": False},
            status="success",
        )
        daily_rows = [
            SimpleNamespace(
                portfolio_return=0.1,
                trade_date=d,
                benchmark_return=None,
                turnover=0.2,
                total_exposure=1.0,
                positions={"000300": 1.0},
            )
            for d in DATES
        ]
        stability = svc._compute_stability(row, daily_rows)
        assert stability.turnover_model == TURNOVER_MODEL_LEGACY

    def test_ladder_zero_bp_equals_gross(self) -> None:
        """成本档位 0bp 的净累计收益等于毛累计收益。"""
        svc = BacktestService(db=MagicMock())
        row = BacktestRunModel(
            backtest_id="bt-ladder",
            strategy_id="t_turnover",
            start_date=DATES[0],
            end_date=DATES[-1],
            universe_filter={"mode": "all"},
            params={"_cost_bps": 10.0, "_enable_benchmark": False},
            status="success",
        )
        daily_rows = [
            SimpleNamespace(
                portfolio_return=1.0,
                trade_date=d,
                benchmark_return=None,
                turnover=0.5,
                total_exposure=1.0,
                positions={"000300": 1.0},
            )
            for d in DATES
        ]
        stability = svc._compute_stability(row, daily_rows)
        gross = stability.cost_ladder[0]
        assert gross.cost_bps == 0.0
        expected = (1.0 + 0.01) ** len(DATES) * 100 - 100
        assert round(gross.net_cumulative_return_pct, 4) == round(expected, 4)
        # 成本越高净收益越低
        assert [entry.net_annualized_return_pct for entry in stability.cost_ladder] == sorted(
            [entry.net_annualized_return_pct for entry in stability.cost_ladder],
            reverse=True,
        )
