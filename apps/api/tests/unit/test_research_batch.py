"""研究批量评估（D-1）单元测试。

覆盖三件事：

1. 变体文件解析：完整 config、patch（点号 / 列表下标路径）、非法结构直接报错；
2. `persist=False` 不落库：日结果与指数结果都不写库、不落最终状态，
   但**计算结果与落库路径完全一致**（同一条执行路径）；
3. 结果摘要与 Δ 差异块：口径指纹与"劣化窗口占比"可用于探索结论。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.research_batch_service import (
    ResearchBatchService,
    ResearchVariant,
    _delta_block,
    _net_digest,
)

DATES = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]


def _baseline_config() -> dict[str, Any]:
    """构造最小基线配置（含列表型过滤规则，用于验证 patch 下标路径）。"""
    return {
        "schema_version": "1",
        "index_codes": ["000300"],
        "score": {"factors": {"return_5d": 1.0}},
        "filters": {
            "logic": "and",
            "rules": [
                {"factor": "close_price", "operator": "gt", "compare_to": "ma_10d"},
                {"factor": "return_5d", "operator": "gt", "value": -5},
            ],
        },
        "rank": {"top_n": 1},
        "portfolio": {"method": "equal_weight"},
        "risk": {"max_asset_weight": 1.0, "max_portfolio_exposure": 1},
    }


class TestParseVariants:
    """变体文件解析。"""

    def test_patch_by_dotted_path_with_list_index(self) -> None:
        """patch 支持 ``filters.rules[1].value`` 形式的列表下标路径。"""
        variants = ResearchBatchService.parse_variants(
            _baseline_config(),
            {"variants": [{"label": "loose", "patch": {"filters.rules[1].value": -15}}]},
        )
        assert len(variants) == 1
        assert variants[0].config["filters"]["rules"][1]["value"] == -15
        # 深拷贝：不得污染基线
        assert _baseline_config()["filters"]["rules"][1]["value"] == -5

    def test_patch_appends_list_item(self) -> None:
        """``rules[+]`` 追加一条规则（结构改动不必提供整份 config）。"""
        variants = ResearchBatchService.parse_variants(
            _baseline_config(),
            {
                "variants": [
                    {
                        "label": "add_breadth",
                        "patch": {
                            "filters.rules[+]": {
                                "factor": "breadth_ma_pct",
                                "op": "gt",
                                "value": 30,
                            }
                        },
                    }
                ]
            },
        )
        rules = variants[0].config["filters"]["rules"]
        assert len(rules) == 3
        assert rules[2]["factor"] == "breadth_ma_pct"
        # 其余模块保持基线内容（config 整体替换会丢掉 score/portfolio 等）
        assert variants[0].config["score"] == _baseline_config()["score"]
        assert len(_baseline_config()["filters"]["rules"]) == 2

    def test_patch_append_to_non_list_rejected(self) -> None:
        """对非列表字段使用 ``[+]`` 直接报错。"""
        with pytest.raises(ValueError, match="无法写入"):
            ResearchBatchService.parse_variants(
                _baseline_config(), [{"label": "bad", "patch": {"rank[+]": 1}}]
            )

    def test_full_config_variant(self) -> None:
        """直接给完整 config 时按原样使用。"""
        custom = _baseline_config()
        custom["rank"] = {"top_n": 3}
        variants = ResearchBatchService.parse_variants(
            _baseline_config(), [{"label": "top3", "config": custom}]
        )
        assert variants[0].config["rank"]["top_n"] == 3
        assert variants[0].kind == "manual"

    def test_patch_applied_after_config(self) -> None:
        """同时给 config 与 patch 时，patch 后应用。"""
        variants = ResearchBatchService.parse_variants(
            _baseline_config(),
            [
                {
                    "label": "combo",
                    "config": _baseline_config(),
                    "patch": {"risk.max_portfolio_exposure": 0.8},
                }
            ],
        )
        assert variants[0].config["risk"]["max_portfolio_exposure"] == 0.8
        assert variants[0].kind == "patch"

    def test_unknown_field_rejected(self) -> None:
        """未知字段直接报错，避免把拼写错误静默当成"没改"。"""
        with pytest.raises(ValueError, match="未知字段"):
            ResearchBatchService.parse_variants(
                _baseline_config(), [{"label": "x", "patches": {"rank.top_n": 2}}]
            )

    def test_missing_label_and_duplicate_rejected(self) -> None:
        """缺少 label 或 label 重复都直接报错。"""
        with pytest.raises(ValueError, match="缺少 label"):
            ResearchBatchService.parse_variants(_baseline_config(), [{"patch": {}}])
        with pytest.raises(ValueError, match="标签重复"):
            ResearchBatchService.parse_variants(
                _baseline_config(),
                [{"label": "a", "patch": {"rank.top_n": 2}}, {"label": "a"}],
            )

    def test_invalid_patch_path_rejected(self) -> None:
        """patch 路径不存在时给出带变体标签的错误，而不是静默跳过。"""
        with pytest.raises(ValueError, match="无法写入"):
            ResearchBatchService.parse_variants(
                _baseline_config(), [{"label": "bad", "patch": {"rank.top_n.deep": 1}}]
            )

    def test_empty_payload_rejected(self) -> None:
        """空列表或无 variants 的对象直接报错。"""
        with pytest.raises(ValueError, match="非空"):
            ResearchBatchService.parse_variants(_baseline_config(), {"variants": []})


def _stub_loop_service(positions_by_index: list[dict[str, float]]) -> tuple[BacktestService, Any]:
    """构造逐日返回指定持仓的内存回测服务（复用 test_backtest_turnover 的思路）。"""
    svc = BacktestService(db=MagicMock())
    svc._ensure_market_scope_bars = MagicMock()
    svc._get_lookback_days = MagicMock(return_value=90)
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
    svc._backtest_repo = MagicMock()
    row = BacktestRunModel(
        backtest_id="bt-research",
        strategy_id="t_research",
        start_date=DATES[0],
        end_date=DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300"]},
        params={"_execution_model": "t_plus_1_open", "_data_quality_mode": "warn"},
        status="running",
    )
    return svc, {"row": row}


def _config() -> StrategyConfig:
    """构造最小可运行配置。"""
    return StrategyConfig(
        strategy_id="t_research",
        display_name="t_research",
        index_codes=["000300"],
        score=ScoreConfig(factors={"return_5d": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight"),
        risk=RiskConfig(max_asset_weight=1.0),
    )


class TestPersistFalse:
    """`persist=False` 不落库但结果与落库路径一致。"""

    def test_no_database_writes(self) -> None:
        """不写日结果、不写指数结果、不落最终状态。"""
        svc, ctx = _stub_loop_service([{"000300": 1.0}, {"000300": 1.0}, {"000300": 1.0}])
        outcome = svc._run_backtest_loop(
            "bt-research", ctx["row"], _config(), persist=False
        )

        assert svc._backtest_repo.add_daily_result.call_count == 0
        assert svc._backtest_repo.add_index_result.call_count == 0
        assert svc._backtest_repo.mark_success.call_count == 0
        assert svc._backtest_repo.update_progress.call_count == 0
        # 逐日结果原样返回给调用方（净口径与稳健性指标现算）
        assert len(outcome["daily_rows"]) == len(DATES)
        assert "metrics" in outcome

    def test_metrics_match_persisted_path(self) -> None:
        """同一配置下，落库与不落库两条路径的毛口径指标必须完全一致。"""
        positions = [{"000300": 1.0}, {}, {"000300": 1.0}]
        persisted_svc, persisted_ctx = _stub_loop_service(positions)
        memory_svc, memory_ctx = _stub_loop_service(positions)

        persisted_svc._run_backtest_loop("bt-a", persisted_ctx["row"], _config(), persist=True)
        memory_outcome = memory_svc._run_backtest_loop(
            "bt-b", memory_ctx["row"], _config(), persist=False
        )

        persisted_rows = persisted_svc._backtest_repo.add_daily_result.call_args_list
        memory_rows = memory_outcome["daily_rows"]
        assert len(persisted_rows) == len(memory_rows)
        for persisted_call, memory_row in zip(persisted_rows, memory_rows, strict=True):
            persisted_row = persisted_call.args[0]
            assert persisted_row.portfolio_return == memory_row.portfolio_return
            assert persisted_row.turnover == memory_row.turnover
            assert persisted_row.total_exposure == memory_row.total_exposure
        assert list(memory_outcome["params"].keys()) == list(memory_ctx["row"].params.keys())

    def test_caches_reuse_prepared_data_across_variants(self) -> None:
        """共享缓存命中后不重复加载行情（批量评估的速度来源）。"""
        from quant_etf_api.services.backtest_service import BacktestRunCaches

        svc, ctx = _stub_loop_service([{"000300": 1.0}, {"000300": 1.0}, {"000300": 1.0}])
        caches = BacktestRunCaches()
        svc._run_backtest_loop("bt-1", ctx["row"], _config(), persist=False, caches=caches)
        first_calls = svc._factor_provider.precompute_backtest_factors.call_count

        second_row = BacktestRunModel(
            backtest_id="bt-2",
            strategy_id="t_research",
            start_date=ctx["row"].start_date,
            end_date=ctx["row"].end_date,
            universe_filter=dict(ctx["row"].universe_filter),
            params=dict(ctx["row"].params),
            status="running",
        )
        svc._run_backtest_loop("bt-2", second_row, _config(), persist=False, caches=caches)

        # 第二个变体命中因子缓存，不再重算
        assert svc._factor_provider.precompute_backtest_factors.call_count == first_calls


class TestDeltaBlock:
    """变体相对基线的差异块。"""

    def test_worse_window_ratio_counts_negative_deltas(self) -> None:
        """逐窗口净夏普差为负的窗口计入劣化占比。"""
        baseline = {"net_metrics": {"net_sharpe_ratio": 0.5, "net_annualized_return_pct": 5.0}}
        variant = {"net_metrics": {"net_sharpe_ratio": 0.2, "net_annualized_return_pct": 3.0}}
        base_windows = [
            {"label": "W1", "net_metrics": {"net_sharpe_ratio": 0.5}},
            {"label": "W2", "net_metrics": {"net_sharpe_ratio": 0.5}},
        ]
        var_windows = [
            {"label": "W1", "net_metrics": {"net_sharpe_ratio": 0.2}},
            {"label": "W2", "net_metrics": {"net_sharpe_ratio": 0.9}},
        ]

        block = _delta_block(baseline, variant, base_windows, var_windows)

        assert block["delta_net_sharpe_ratio"] == -0.3
        assert block["delta_net_annualized_return_pct"] == -2.0
        assert block["worse_window_ratio"] == 0.5

    def test_missing_values_give_none(self) -> None:
        """缺少可比数值时返回 None，而不是编造 0。"""
        block = _delta_block(
            {"net_metrics": {}}, {"net_metrics": {}}, [], []
        )
        assert block["delta_net_sharpe_ratio"] is None
        assert block["worse_window_ratio"] is None


class TestDigests:
    """结果摘要字段。"""

    def test_net_digest_contains_cost_ladder(self) -> None:
        """净口径摘要包含多档成本并列结果（成本是本轮最关键的结论维度）。"""
        stability = SimpleNamespace(
            cost_bps=10.0,
            net_cumulative_return_pct=52.4,
            net_annualized_return_pct=4.5,
            net_sharpe_ratio=0.35,
            net_excess_return_pct=1.2,
            annualized_turnover=13.0,
            cost_drag_pct_per_year=1.3,
            max_drawdown_pct=-18.0,
            year_return_share_max=0.4,
            ex_best_year_sharpe_ratio=0.2,
            segment_sharpe_positive_ratio=0.6,
            cost_ladder=[
                SimpleNamespace(
                    cost_bps=0.0, net_annualized_return_pct=5.8, net_sharpe_ratio=0.44
                ),
                SimpleNamespace(
                    cost_bps=10.0, net_annualized_return_pct=4.5, net_sharpe_ratio=0.35
                ),
            ],
        )

        digest = _net_digest(stability)

        assert digest["annualized_turnover"] == 13.0
        assert [entry["cost_bps"] for entry in digest["cost_ladder"]] == [0.0, 10.0]

    def test_research_variant_is_immutable(self) -> None:
        """变体数据结构不可变，避免批量执行中被就地改写。"""
        variant = ResearchVariant(label="a", config={"rank": {"top_n": 1}})
        with pytest.raises(Exception):
            variant.label = "b"  # type: ignore[misc]
