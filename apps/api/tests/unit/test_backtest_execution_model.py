"""回测执行模型的收益归属与口径字段单元测试。

覆盖：
- ``t_plus_1_close``：T 日信号在 T+1 收盘成交，逐日收益必须使用"今日收盘成交
  之后的仓位"，而不是更旧的仓位（历史缺陷：等价于 T+2 收盘执行）；
- ``t_plus_1_open``：对照组，调仓日收益 = 旧仓位隔夜段 + 新仓位日内段；
- 基准对比口径字段（累计超额 / 年化超额 / 基准上涨日占比）与预热期字段。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from quant_etf_api.domain.research.metrics import compute_performance_metrics
from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService

DATES = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)]
# 收盘价：每段 +10%；开盘价故意与收盘价不同，用于区分"隔夜段/日内段"口径
CLOSES = [100.0, 110.0, 121.0, 133.1]
OPENS = [100.0, 105.0, 115.0, 125.0]


def _bar(close: float, open_price: float) -> SimpleNamespace:
    """构造最小行情对象。"""
    return SimpleNamespace(
        close_price=close, open_price=open_price, high_price=close, low_price=close
    )


def _config() -> StrategyConfig:
    """构造单资产、每日调仓的最小策略配置。"""
    return StrategyConfig(
        strategy_id="t_exec",
        display_name="t_exec",
        index_codes=["000300"],
        score=ScoreConfig(factors={"return_5d": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight", default_exposure=1.0),
    )


def _stub_service(
    *,
    execution_model: str = "t_plus_1_open",
    enable_benchmark: bool = False,
    position_weight: float = 1.0,
    factor_missing_first: bool = False,
    universe: list[dict[str, Any]] | None = None,
) -> tuple[BacktestService, dict[str, Any]]:
    """构造可在内存中跑完整主循环的 BacktestService（不连库）。

    Args:
        execution_model: 执行模型。
        enable_benchmark: 是否启用基准。
        position_weight: 桩引擎给出的单资产目标权重。
        factor_missing_first: 首个交易日的因子值是否缺失（用于制造预热期）。
        universe: 自定义标的池；None 时使用单指数 000300。

    Returns:
        (service, context) —— context 含 row 与逐日结果列表。
    """
    svc = BacktestService(db=MagicMock())
    svc._ensure_market_scope_bars = MagicMock()
    svc._get_lookback_days = MagicMock(return_value=90)
    svc._write_index_results = MagicMock(return_value=(0, 0))

    bars: dict[tuple[str, date], Any] = {}
    for i, d in enumerate(DATES):
        bars[("000300", d)] = _bar(CLOSES[i], OPENS[i])

    if universe is None:
        universe = [
            {
                "index_code": "000300",
                "name_cn": "000300",
                "category": "broad_index",
                "is_active": True,
            }
        ]
    index_codes = [item["index_code"] for item in universe]
    svc._prepare_backtest_data = MagicMock(
        return_value=(universe, index_codes, list(DATES), bars, {}, {})
    )

    precomputed: dict[date, dict[tuple[str, str], float | None]] = {}
    for i, d in enumerate(DATES):
        value: float | None = 1.0
        if factor_missing_first and i == 0:
            value = None
        precomputed[d] = {("000300", "return_5d"): value}
    svc._factor_provider = MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = precomputed

    def _build_context(config: StrategyConfig, trade_date: date, **kwargs: Any) -> EngineContext:
        """返回最小可用上下文（标的池由候选池决定）。"""
        codes = list(kwargs.get("index_codes") or [])
        return EngineContext(
            trade_date=trade_date,
            universe=[{"index_code": c, "name_cn": c, "category": "broad_index"} for c in codes],
            asset_factors={},
        )

    svc._context_builder = MagicMock()
    svc._context_builder.build.side_effect = _build_context

    def _run_engine(
        config: StrategyConfig, context: EngineContext, include_details: bool = False
    ) -> EngineResult:
        """桩引擎：候选池非空时按给定权重持有 000300。"""
        codes = [item["index_code"] for item in context.universe]
        positions = {"000300": position_weight} if codes else {}
        return EngineResult(
            trade_date=context.trade_date,
            strategy_id=config.strategy_id,
            timing=None,
            scores={c: 1.0 for c in codes},
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
    svc._backtest_repo.add_index_result.side_effect = lambda row: None

    row = BacktestRunModel(
        backtest_id="bt-exec",
        strategy_id="t_exec",
        start_date=DATES[0],
        end_date=DATES[-1],
        universe_filter={"mode": "subset", "index_codes": index_codes},
        params={
            "_execution_model": execution_model,
            "_data_quality_mode": "warn",
            "_enable_benchmark": enable_benchmark,
            "_benchmark_index_code": "000300",
        },
        status="running",
    )
    return svc, {"row": row, "rows": collected}


class TestCloseExecutionAttribution:
    """T+1 收盘执行的收益归属。"""

    def test_return_uses_position_executed_at_today_close(self) -> None:
        """逐日收益必须用"今日收盘成交后的仓位"，建仓当日不能记 0 收益。"""
        svc, ctx = _stub_service(execution_model="t_plus_1_close")
        svc._run_backtest_loop("bt-close", ctx["row"], _config())
        rows = ctx["rows"]
        assert len(rows) == len(DATES)

        # 首个决策日：成交发生在次日收盘，当日无仓位、0 收益
        assert rows[0].portfolio_return == 0.0
        assert rows[0].executed_positions is None
        assert rows[0].total_exposure == 0.0

        # 第二行：D0 的信号在 D1 收盘成交，吃 [close_D1, close_D2] = +10%
        # （历史缺陷：这里会用空仓位算出 0，把 +10% 推迟到下一行）
        assert rows[1].portfolio_return == pytest.approx(10.0)
        assert rows[1].positions == {"000300": 1.0}
        assert rows[1].executed_positions == {"000300": 1.0}
        assert rows[1].total_exposure == 1.0
        assert rows[1].cash_ratio == 0.0
        # 建仓腿同样计入换手（Σ|Δw|/2 = 0.5）
        assert rows[1].turnover == pytest.approx(0.5)

        # 第三行：仓位不变 → 无换手，仍吃 +10%
        assert rows[2].portfolio_return == pytest.approx(10.0)
        assert rows[2].turnover is None

        # 最后一日无下一交易日 → 0 收益
        assert rows[3].portfolio_return == 0.0

    def test_open_execution_keeps_overnight_and_intraday_legs(self) -> None:
        """对照组：T+1 开盘执行，调仓日收益拆隔夜段与日内段。"""
        svc, ctx = _stub_service(execution_model="t_plus_1_open")
        svc._run_backtest_loop("bt-open", ctx["row"], _config())
        rows = ctx["rows"]

        # 首个调仓日：旧仓位为空，只吃新仓位的日内段 [open_D1, close_D1]
        assert rows[0].portfolio_return == pytest.approx(round((110 / 105 - 1) * 100, 4))
        # 第二个调仓日：旧仓位隔夜段 [close_D1, open_D2] + 新仓位日内段 [open_D2, close_D2]
        # （两段各自四舍五入后再相加，与领域函数口径一致）
        overnight = round((115 / 110 - 1) * 100, 4)
        intraday = round((121 / 115 - 1) * 100, 4)
        assert rows[1].portfolio_return == pytest.approx(round(overnight + intraday, 4))
        # 目标仓位未变 → 无换手（落库为 None）
        assert rows[1].turnover is None


class TestMetricsCaliber:
    """基准对比与预热期口径字段。"""

    def test_annualized_excess_and_benchmark_up_days(self) -> None:
        """年化超额按"策略年化 − 基准年化"计算，基准上涨日占比可核。"""
        svc, ctx = _stub_service(enable_benchmark=True, position_weight=0.5)
        svc._run_backtest_loop("bt-bench", ctx["row"], _config())
        metrics = svc._backtest_repo.mark_success.call_args.args[1]

        # 基准（买入持有 000300）：D0~D2 各 +10%，最后一日记 0 → 3/4 = 75%
        assert metrics["benchmark_up_days_pct"] == pytest.approx(75.0)
        assert metrics["benchmark_return_pct"] == pytest.approx(33.1)
        # 累计口径超额 = 策略累计 − 基准累计
        assert metrics["excess_return_pct"] == pytest.approx(
            round(metrics["cumulative_return_pct"] - 33.1, 2)
        )
        # 年化口径超额（毛口径）= 策略年化 − 基准年化
        bench_returns = [10.0, 10.0, 10.0, 0.0]
        expected_annualized_excess = round(
            metrics["annualized_return_pct"]
            - compute_performance_metrics(bench_returns).annualized_return_pct,
            2,
        )
        assert metrics["annualized_excess_return_pct"] == pytest.approx(
            expected_annualized_excess, abs=0.01
        )
        assert metrics["annualized_excess_return_pct"] < 0  # 半仓必然跑输满仓基准

    def test_warmup_days_surface_in_stability(self) -> None:
        """预热交易日数随 params 落到稳健性块（仅提示，不改指标区间）。"""
        svc, ctx = _stub_service(factor_missing_first=True)
        svc._run_backtest_loop("bt-warm", ctx["row"], _config())
        assert ctx["row"].params["_warmup_trading_days"] == 1

        stability = svc._compute_stability(ctx["row"], ctx["rows"])
        assert stability.warmup_trading_days == 1
