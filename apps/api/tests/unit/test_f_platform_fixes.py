"""F 类修复的接口/会话层单元测试（F-9 / F-11 / F-15 / F-16 / F-17 / F-18）。

- F-9：research batch 输出「变体 × 指标」排名表；
- F-11：策略详情的顶层 index_codes 与 config_json 一致；
- F-15：optimization show 直接返回 7 项验收清单；
- F-16：优化会话指标里带净成本口径；
- F-17：promote 时把版本历史追加进策略描述；
- F-18：optimization start 的区间缺省覆盖整个研究期。
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quant_etf_api.infra.db.models.core import StrategyOptimizationModel
from quant_etf_api import cli as cli_module
from quant_etf_api.services.optimization_service import (
    OptimizationService,
    _append_version_history,
)
from quant_etf_api.services.research_batch_service import ResearchBatchResult
from quant_etf_api.services.strategy_config_service import StrategyConfigService


class TestResearchSummaryTable:
    """F-9：变体排名表。"""

    def test_table_lists_variants_with_net_delta(self) -> None:
        """表格包含净口径、Δ 与劣化窗口比例，且不输出完整 JSON。"""
        result = ResearchBatchResult(
            windows=[{"label": "W0", "start": "2024-01-02", "end": "2024-06-28"}],
            baseline_label="baseline",
            cost_bps=10.0,
            variants=[
                {
                    "label": "baseline",
                    "aggregate": {
                        "metrics": {"annualized_return_pct": 6.0, "sharpe_ratio": 0.5},
                        "net_metrics": {
                            "net_annualized_return_pct": 4.5,
                            "net_sharpe_ratio": 0.35,
                            "annualized_turnover": 15.0,
                        },
                    },
                    "vs_baseline": {"per_window": [{"label": "W0", "delta_net_sharpe": 0.0}]},
                },
                {
                    "label": "drop_ma17",
                    "aggregate": {
                        "metrics": {"annualized_return_pct": 7.0, "sharpe_ratio": 0.6},
                        "net_metrics": {
                            "net_annualized_return_pct": 5.4,
                            "net_sharpe_ratio": 0.42,
                            "annualized_turnover": 15.5,
                        },
                    },
                    "vs_baseline": {
                        "delta_net_annualized_return_pct": 0.9,
                        "delta_net_sharpe_ratio": 0.07,
                        "worse_window_ratio": 0.2,
                        "per_window": [{"label": "W0", "delta_net_sharpe": 0.07}],
                    },
                },
            ],
        )

        table = result.to_summary_table()

        lines = table.splitlines()
        assert "cost_bps=10.0" in lines[1]
        assert "variant" in lines[2] and "netShp" in lines[2]
        assert any(line.startswith("drop_ma17") for line in lines)
        drop_line = next(line for line in lines if line.startswith("drop_ma17"))
        assert "0.42" in drop_line and "0.07" in drop_line and "W0:0.07" in drop_line


class TestStrategyDetailIndexCodes:
    """F-11：详情顶层 index_codes。"""

    def test_detail_fills_index_codes_from_config(self) -> None:
        """详情返回的 index_codes 必须与 config_json 一致。"""
        svc = object.__new__(StrategyConfigService)
        svc._repo = MagicMock()
        svc._repo.find_by_id.return_value = SimpleNamespace(
            strategy_id="s1",
            display_name="策略",
            version="1.0.0",
            frequency="daily",
            description="",
            status="active",
            is_starred=False,
            config_json={"index_codes": ["000300", "000905"]},
            created_at=None,
            updated_at=None,
            is_variant=False,
            source_batch_id=None,
            validation_consumed_at=None,
            validation_consumed_note=None,
        )

        detail = svc.get_config("s1")

        assert detail is not None
        assert detail.index_codes == ["000300", "000905"]


def _make_session(**overrides: object) -> StrategyOptimizationModel:
    """构造优化会话 ORM 行（含逐折聚合）。"""
    fields: dict[str, object] = {
        "optimization_id": "opt1",
        "strategy_id": "base",
        "baseline_version": "1.0.0",
        "baseline_config_hash": "h",
        "candidate_strategy_id": "cand",
        "candidate_version": "1.0.1",
        "candidate_config_hash": "h2",
        "hypothesis": "删除冗余过滤",
        "status": "evaluated",
        "start_date": date(2016, 1, 1),
        "end_date": date(2025, 12, 31),
        "fold_summary": {
            "total_folds": 2,
            "metrics": {
                "sharpe_ratio": {
                    "baseline_mean": 0.30,
                    "candidate_mean": 0.40,
                    "candidate_wins": 2,
                    "total_folds": 2,
                },
                "max_drawdown_pct": {"baseline_mean": -18.0, "candidate_mean": -17.0},
                "cumulative_return_pct": {"baseline_mean": 10.0, "candidate_mean": 12.0},
            },
        },
        "created_at": None,
        "updated_at": None,
    }
    fields.update(overrides)
    return StrategyOptimizationModel(**fields)


class TestOptimizationShowChecklist:
    """F-15：详情里直接给出验收清单。"""

    def test_show_includes_acceptance_checklist(self, monkeypatch) -> None:
        """show 返回 7 项清单（含净成本与邻域项），不必先调用 finish。"""
        svc = OptimizationService(db=MagicMock())
        session = _make_session()
        svc._repo = MagicMock()
        svc._repo.find_by_id.return_value = session
        svc._backtest_repo = MagicMock()
        svc._backtest_repo.find_by_id.return_value = None
        monkeypatch.setattr(
            OptimizationService, "_check_neighborhood", lambda self, session: {
                "key": "neighborhood",
                "description": "参数邻域无方向反转",
                "pass": True,
                "evidence": None,
            }
        )
        monkeypatch.setattr(
            OptimizationService, "_check_net_cost", lambda self, s: {
                "key": "net_cost_nonnegative",
                "description": "净成本口径验证窗平均夏普 ≥ 基线",
                "pass": True,
                "evidence": None,
            }
        )

        payload = svc.show("opt1")

        assert payload is not None
        checklist = payload["acceptance_checklist"]
        assert len(checklist) == 7
        assert {item["key"] for item in checklist} >= {
            "sharpe_nonnegative",
            "net_cost_nonnegative",
            "neighborhood",
        }


class TestOptimizationNetMetrics:
    """F-16：会话指标带净口径。"""

    def test_collect_metrics_merges_net_fields(self) -> None:
        """逐折指标里应出现净夏普、净年化与年化换手。"""
        svc = OptimizationService(db=MagicMock())
        svc._backtest_repo = MagicMock()
        run_row = SimpleNamespace(status="success", metrics={"sharpe_ratio": 1.0}, params={})
        daily = [
            SimpleNamespace(
                portfolio_return=0.1,
                trade_date=date(2024, 1, 2) + timedelta(days=i),
                turnover=0.05,
                benchmark_return=0.05,
            )
            for i in range(10)
        ]
        svc._backtest_repo.find_by_id.return_value = run_row
        svc._backtest_repo.find_daily_results.return_value = daily

        metrics = svc._collect_metrics("bt-1")

        assert metrics is not None
        assert metrics["sharpe_ratio"] == 1.0
        assert metrics["net_sharpe_ratio"] is not None
        assert metrics["annualized_turnover"] == pytest.approx(12.6, rel=1e-6)


class TestPromoteVersionHistory:
    """F-17：promote 同步更新策略描述。"""

    def test_append_version_history_contains_version_and_wins(self) -> None:
        """追加的版本记录包含版本号、假设与逐折胜出情况。"""
        text = _append_version_history(
            "旧描述",
            "1.7.0",
            "删除 close_price>ma_17d 过滤",
            {
                "metrics": {
                    "sharpe_ratio": {
                        "candidate_wins": 4,
                        "total_folds": 5,
                        "baseline_mean": 0.3318,
                        "candidate_mean": 0.4116,
                    }
                }
            },
        )

        assert text.startswith("旧描述；")
        assert "v1.7.0" in text
        assert "删除 close_price>ma_17d 过滤" in text
        assert "4/5" in text

    def test_finish_promote_updates_description(self) -> None:
        """finish(promote=True) 必须把描述一并写回基线。"""
        svc = OptimizationService(db=MagicMock())
        session = _make_session()
        svc._repo = MagicMock()
        svc._repo.find_by_id.return_value = session
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.side_effect = [
            SimpleNamespace(
                strategy_id="cand",
                config_json={"score": {"factors": {"return_20d": 1.0}}},
                description="候选",
            ),
            SimpleNamespace(
                strategy_id="base",
                config_json={"score": {"factors": {"return_20d": 0.5}}},
                description="基线描述",
            ),
        ]

        svc.finish("opt1", verdict="accept", promote=True, strict=False)

        update = svc._config_svc.update_config.call_args
        assert update.args[0] == "base"
        payload = update.args[1]
        assert payload.version == "1.0.1"
        assert "v1.0.1" in payload.description
        assert payload.description.startswith("基线描述")


class TestOptimizationDefaultPeriod:
    """F-18：optimization start 的区间缺省。"""

    def test_start_defaults_to_research_period(self, monkeypatch) -> None:
        """不传 --start/--end 时，会话区间应覆盖整个研究期。"""
        captured: dict[str, object] = {}

        class _FakeOptimizationService:
            """替身优化服务：记录 start 参数。"""

            def __init__(self, db: object) -> None:
                """初始化替身。"""

            def start(self, **kwargs: object) -> dict[str, object]:
                """记录参数并返回空结果。"""
                captured.update(kwargs)
                return {}

        monkeypatch.setattr(cli_module, "SessionLocal", lambda: MagicMock())
        monkeypatch.setattr(cli_module, "OptimizationService", _FakeOptimizationService)
        monkeypatch.setattr(cli_module, "_read_json_file", lambda path: {"a": 1})
        monkeypatch.setattr(cli_module, "_emit", lambda data, as_json: None)
        args = argparse.Namespace(
            subcommand="start",
            strategy_id="base",
            candidate_file="cand.json",
            hypothesis="h",
            start=None,
            end=None,
            folds=5,
            candidate_strategy_id=None,
            candidate_version="1.7.0",
            no_json=True,
        )

        cli_module._run_optimization(args)

        from quant_etf_api.config.settings import get_settings

        assert captured["start_date"] == date.fromisoformat(
            get_settings().research_period_start
        )
        assert captured["end_date"] == date.fromisoformat(get_settings().research_period_end)
