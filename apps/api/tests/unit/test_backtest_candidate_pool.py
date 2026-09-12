"""回测候选池口径统一（B11）单元测试。

覆盖：
- `_build_candidate_pool` 的入池条件与剔除原因（缺收盘/缺因子/缺高低价/缺次日开盘）
- 回归：同一策略配置在 warn / strict 两种数据质量口径下逐日结果完全一致
  （历史缺陷：宽松口径把不可交易标的放进横截面 z-score 池，导致选股与绩效分叉）
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import (
    FilterConfig,
    FilterRule,
    PortfolioConfig,
    RankConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService

DATES = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]


def _bar(close: float, open_price: float | None) -> SimpleNamespace:
    """构造最小行情对象（仅含候选池判定所需字段）。"""
    return SimpleNamespace(
        close_price=close,
        open_price=open_price,
        high_price=close,
        low_price=close,
    )


def _make_service(specs: list[str] | None = None) -> BacktestService:
    """构建测试用 BacktestService（Mock 会话 + 桩注册表）。"""
    svc = BacktestService(db=MagicMock())
    factor_ids = specs if specs is not None else ["return_5d", "ma_5d"]
    svc._registry = MagicMock()
    svc._registry.specs.return_value = [
        SimpleNamespace(factor_id=fid, value_shape="asset") for fid in factor_ids
    ]
    return svc


def _config(
    score_factors: dict[str, float] | None = None,
    filters: FilterConfig | None = None,
) -> StrategyConfig:
    """构造最小可运行策略配置。"""
    return StrategyConfig(
        strategy_id="t_pool",
        display_name="t_pool",
        index_codes=["000300", "000905"],
        score=ScoreConfig(factors=score_factors or {"return_5d": 1.0}),
        filters=filters,
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight"),
        risk=RiskConfig(max_asset_weight=1.0),
    )


class TestBuildCandidatePool:
    """候选池入池条件与剔除原因。"""

    def test_excludes_missing_next_open_for_open_execution(self) -> None:
        """T+1 开盘执行下，次日开盘价缺失的标的应被剔除并记录 MISSING_OPEN。"""
        svc = _make_service()
        bars = {
            ("000300", DATES[0]): _bar(100.0, 100.0),
            ("000300", DATES[1]): _bar(101.0, 101.0),
            ("000905", DATES[0]): _bar(200.0, 200.0),
            ("000905", DATES[1]): _bar(201.0, None),
        }
        factors = {
            ("000300", "return_5d"): 1.0,
            ("000905", "return_5d"): 2.0,
        }

        codes, reasons = svc._build_candidate_pool(
            _config(), DATES[0], DATES[1], ["000300", "000905"], bars, factors, "t_plus_1_open"
        )

        assert codes == ["000300"]
        assert reasons["000905"] == {"MISSING_OPEN"}

    def test_close_execution_does_not_require_open(self) -> None:
        """T+1 收盘执行只依赖收盘价，缺开盘价不应剔除标的。"""
        svc = _make_service()
        bars = {
            ("000300", DATES[0]): _bar(100.0, 100.0),
            ("000300", DATES[1]): _bar(101.0, 101.0),
            ("000905", DATES[0]): _bar(200.0, 200.0),
            ("000905", DATES[1]): _bar(201.0, None),
        }
        factors = {("000300", "return_5d"): 1.0, ("000905", "return_5d"): 2.0}

        codes, _ = svc._build_candidate_pool(
            _config(), DATES[0], DATES[1], ["000300", "000905"], bars, factors, "t_plus_1_close"
        )

        assert codes == ["000300", "000905"]

    def test_excludes_missing_factor_and_missing_close(self) -> None:
        """因子缺失记 MISSING_FACTOR，当日收盘缺失记 MISSING_CLOSE。"""
        svc = _make_service()
        bars = {
            ("000300", DATES[0]): _bar(100.0, 100.0),
            ("000300", DATES[1]): _bar(101.0, 101.0),
            ("000905", DATES[0]): _bar(200.0, 200.0),
            ("000905", DATES[1]): _bar(201.0, 201.0),
            ("399001", DATES[1]): _bar(300.0, 300.0),
        }
        factors = {
            ("000300", "return_5d"): 1.0,
            ("000905", "return_5d"): None,
            ("399001", "return_5d"): 3.0,
        }

        codes, reasons = svc._build_candidate_pool(
            _config(),
            DATES[0],
            DATES[1],
            ["000300", "000905", "399001"],
            bars,
            factors,
            "t_plus_1_open",
        )

        assert codes == ["000300"]
        assert reasons["000905"] == {"MISSING_FACTOR"}
        assert reasons["399001"] == {"MISSING_CLOSE"}

    def test_requires_high_low_for_atr_like_factors(self) -> None:
        """引用 ATR/Donchian 类因子时，最高/最低价缺失应剔除标的。"""
        svc = _make_service(specs=["atr_14d"])
        bars = {
            ("000300", DATES[0]): SimpleNamespace(
                close_price=100.0, open_price=100.0, high_price=None, low_price=None
            ),
            ("000300", DATES[1]): _bar(101.0, 101.0),
        }
        factors = {("000300", "atr_14d"): 1.0}

        codes, reasons = svc._build_candidate_pool(
            _config(score_factors={"atr_14d": 1.0}),
            DATES[0],
            DATES[1],
            ["000300"],
            bars,
            factors,
            "t_plus_1_open",
        )

        assert codes == []
        assert reasons["000300"] == {"MISSING_HIGH_LOW"}

    def test_requires_high_low_for_rsrs_and_price_position_factors(self) -> None:
        """引用 rsrs / price_position_ir 因子时，最高/最低价缺失应报 MISSING_HIGH_LOW。"""
        for factor_id in ("rsrs", "price_position_ir_60d"):
            svc = _make_service(specs=[factor_id])
            bars = {
                ("000300", DATES[0]): SimpleNamespace(
                    close_price=100.0, open_price=100.0, high_price=None, low_price=None
                ),
                ("000300", DATES[1]): _bar(101.0, 101.0),
            }
            factors = {("000300", factor_id): 1.0}

            codes, reasons = svc._build_candidate_pool(
                _config(score_factors={factor_id: 1.0}),
                DATES[0],
                DATES[1],
                ["000300"],
                bars,
                factors,
                "t_plus_1_open",
            )

            assert codes == [], factor_id
            assert reasons["000300"] == {"MISSING_HIGH_LOW"}, factor_id

    def test_last_trading_day_has_no_next_day_requirement(self) -> None:
        """回测最后一日无下一交易日，不再校验次日行情。"""
        svc = _make_service()
        bars = {("000300", DATES[0]): _bar(100.0, None)}
        factors = {("000300", "return_5d"): 1.0}

        codes, reasons = svc._build_candidate_pool(
            _config(), DATES[0], None, ["000300"], bars, factors, "t_plus_1_open"
        )

        assert codes == ["000300"]
        assert reasons == {}

    def test_filter_compare_to_factor_is_required(self) -> None:
        """过滤规则 compare_to 引用的因子缺失时，标的同样被剔除。"""
        svc = _make_service(specs=["close_price", "ma_5d"])
        bars = {
            ("000300", DATES[0]): _bar(100.0, 100.0),
            ("000300", DATES[1]): _bar(101.0, 101.0),
        }
        factors = {("000300", "close_price"): 100.0}  # ma_5d 缺失
        filters = FilterConfig(
            logic="AND",
            rules=[FilterRule(factor="close_price", op="gt", compare_to="ma_5d")],
        )

        codes, reasons = svc._build_candidate_pool(
            _config(score_factors={"close_price": 1.0}, filters=filters),
            DATES[0],
            DATES[1],
            ["000300"],
            bars,
            factors,
            "t_plus_1_open",
        )

        assert codes == []
        assert reasons["000300"] == {"MISSING_FACTOR"}


def _stub_service_for_loop(quality_mode: str) -> tuple[BacktestService, dict[str, Any]]:
    """构造可在内存中跑完整主循环的 BacktestService（不连库）。"""
    svc = _make_service()
    svc._ensure_market_scope_bars = MagicMock()
    svc._get_lookback_days = MagicMock(return_value=90)
    svc._write_index_results = MagicMock(return_value=(0, 0))

    bars = {
        ("000300", DATES[0]): _bar(100.0, 100.0),
        ("000300", DATES[1]): _bar(101.0, 101.0),
        ("000300", DATES[2]): _bar(102.0, 102.0),
        # 000905 全程只有首个交易日有开盘价：旧口径下 warn 仍会买它并吃掉 0 收益
        ("000905", DATES[0]): _bar(200.0, 200.0),
        ("000905", DATES[1]): _bar(210.0, None),
        ("000905", DATES[2]): _bar(220.0, None),
    }
    universe = [
        {"index_code": "000300", "name_cn": "000300", "category": "broad_index"},
        {"index_code": "000905", "name_cn": "000905", "category": "broad_index"},
    ]
    svc._prepare_backtest_data = MagicMock(
        return_value=(universe, ["000300", "000905"], list(DATES), bars, {}, {})
    )

    precomputed = {
        d: {("000300", "return_5d"): 1.0, ("000905", "return_5d"): 2.0} for d in DATES
    }
    svc._factor_provider = MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = precomputed

    seen_universes: list[list[str]] = []

    def _build_context(config: StrategyConfig, trade_date: date, **kwargs: Any) -> EngineContext:
        """记录每日候选池，返回最小可用上下文。"""
        codes = list(kwargs.get("index_codes") or [])
        seen_universes.append(codes)
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
        """桩引擎：always 选择候选池中最后一个标的（模拟"选股依赖候选池"）。"""
        codes = [item["index_code"] for item in context.universe]
        positions = {codes[-1]: 1.0} if codes else {}
        return EngineResult(
            trade_date=context.trade_date,
            strategy_id=config.strategy_id,
            timing=None,
            scores={c: float(i) for i, c in enumerate(codes)},
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
        backtest_id=f"bt-{quality_mode}",
        strategy_id="t_pool",
        start_date=DATES[0],
        end_date=DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300", "000905"]},
        params={
            "_execution_model": "t_plus_1_open",
            "_data_quality_mode": quality_mode,
            "_enable_benchmark": True,
            "_benchmark_index_code": "000300",
        },
        status="running",
    )
    return svc, {"row": row, "collected": collected, "universes": seen_universes}


class TestQualityModeDoesNotChangeDecisions:
    """回归：warn / strict 两种口径的选股与收益必须完全一致。"""

    def test_identical_daily_results_across_modes(self) -> None:
        """同一配置、同一行情下，两种口径的逐日结果应逐字段一致。"""
        svc_warn, ctx_warn = _stub_service_for_loop("warn")
        svc_strict, ctx_strict = _stub_service_for_loop("strict")

        svc_warn._run_backtest_loop("bt-warn", ctx_warn["row"], _config())
        svc_strict._run_backtest_loop("bt-strict", ctx_strict["row"], _config())

        warn_rows = ctx_warn["collected"]
        strict_rows = ctx_strict["collected"]
        assert len(warn_rows) == len(strict_rows) == len(DATES)
        for a, b in zip(warn_rows, strict_rows):
            assert a.trade_date == b.trade_date
            assert a.portfolio_return == b.portfolio_return
            assert a.positions == b.positions
            assert a.total_exposure == b.total_exposure
            assert a.cash_ratio == b.cash_ratio

        # 两种口径必须使用同一候选池（缺开盘价的 000905 一律不参与选股）
        assert ctx_warn["universes"] == ctx_strict["universes"]
        # 除最后一日（无次日要求）外，缺次日开盘价的 000905 都不参与选股
        assert all(
            "000905" not in codes for codes in ctx_warn["universes"][:-1]
        )

    def test_warn_mode_uses_same_candidate_pool(self) -> None:
        """宽松口径下候选池同样是"可执行池"，不再包含缺次日开盘价的标的。"""
        svc_warn, ctx_warn = _stub_service_for_loop("warn")
        svc_warn._run_backtest_loop("bt-warn", ctx_warn["row"], _config())

        assert ctx_warn["universes"][0] == ["000300"]
        # 最后一日无次日要求，两个标的都入池
        assert ctx_warn["universes"][-1] == ["000300", "000905"]

    def test_warn_mode_emits_aggregated_exclusion_warning(self) -> None:
        """宽松口径输出一条汇总 DATA_EXCLUDED 提示，严格口径逐指数输出。"""
        svc_warn, ctx_warn = _stub_service_for_loop("warn")
        svc_strict, ctx_strict = _stub_service_for_loop("strict")
        svc_warn._run_backtest_loop("bt-warn", ctx_warn["row"], _config())
        svc_strict._run_backtest_loop("bt-strict", ctx_strict["row"], _config())

        warn_metrics = svc_warn._backtest_repo.mark_success.call_args.args[1]
        strict_metrics = svc_strict._backtest_repo.mark_success.call_args.args[1]
        assert warn_metrics == strict_metrics

        warn_codes = [
            w["code"] for w in svc_warn._backtest_repo.mark_success.call_args.kwargs["warnings"]
        ]
        strict_codes = [
            w["code"] for w in svc_strict._backtest_repo.mark_success.call_args.kwargs["warnings"]
        ]
        assert "DATA_EXCLUDED" in warn_codes
        assert "DATA_EXCLUDED" in strict_codes
