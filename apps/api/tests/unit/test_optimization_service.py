"""策略优化会话服务单元测试。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quant_etf_api.infra.db.models.core import StrategyOptimizationModel
from quant_etf_api.schemas.strategy import StrategyValidationResult
from quant_etf_api.services.optimization_service import (
    OptimizationService,
    _compute_fold_summary,
)
from quant_etf_api.services.strategy_config_service import compute_config_hash

_BASELINE_CONFIG = {"score": {"factors": {"return_20d": 1.0}}}
_CANDIDATE_CONFIG = {
    "score": {"factors": {"return_20d": 1.5}, "scoring_mode": "rank"},
    "portfolio": {"method": "winner_take_all", "default_exposure": 1.0},
}


def _make_service() -> OptimizationService:
    """构建测试用 OptimizationService（Mock 会话）。"""
    return OptimizationService(db=MagicMock())


def _make_session(**overrides: object) -> StrategyOptimizationModel:
    """构建测试用优化会话 ORM 行。"""
    fields = {
        "optimization_id": "opt1",
        "strategy_id": "base",
        "baseline_version": "1.0.0",
        "baseline_config_hash": "h",
        "candidate_strategy_id": "cand",
        "candidate_version": "1.0.1",
        "candidate_config_hash": "h",
        "hypothesis": "测试假设",
        "status": "evaluated",
        "start_date": date(2024, 1, 1),
        "end_date": date(2025, 1, 1),
    }
    fields.update(overrides)
    return StrategyOptimizationModel(**fields)


class TestStart:
    """开始优化会话。"""

    def test_creates_draft_candidate_and_session(self) -> None:
        """start 创建草稿候选策略与会话记录。"""
        svc = _make_service()
        svc._repo = MagicMock()
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.side_effect = [
            SimpleNamespace(
                strategy_id="base",
                display_name="基线策略",
                version="1.0.0",
                frequency="daily",
                config_json=dict(_BASELINE_CONFIG),
            ),
            None,  # 候选策略不存在
        ]
        svc._config_svc.get_parsed_config.return_value = MagicMock(portfolio=MagicMock())
        svc._config_svc.validate_config.return_value = StrategyValidationResult(
            valid=True, errors=[]
        )

        result = svc.start(
            strategy_id="base",
            candidate_config=dict(_CANDIDATE_CONFIG),
            hypothesis="提高动量权重",
            start_date=date(2024, 1, 1),
            end_date=date(2025, 1, 1),
        )

        create_req = svc._config_svc.create_config.call_args.args[0]
        assert create_req.status == "draft"
        assert create_req.strategy_id.startswith("base__opt_")
        model = svc._repo.create.call_args.args[0]
        assert model.status == "running"
        assert model.strategy_id == "base"
        assert model.baseline_config_hash == compute_config_hash(_BASELINE_CONFIG)
        assert result["optimization_id"] == model.optimization_id

    def test_missing_hypothesis_raises(self) -> None:
        """缺少假设时报错。"""
        svc = _make_service()
        with pytest.raises(ValueError, match="hypothesis"):
            svc.start(
                "base",
                dict(_CANDIDATE_CONFIG),
                "   ",
                date(2024, 1, 1),
                date(2025, 1, 1),
            )

    def test_duplicate_candidate_id_raises(self) -> None:
        """候选 ID 已存在时报错。"""
        svc = _make_service()
        svc._repo = MagicMock()
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.side_effect = [
            SimpleNamespace(
                strategy_id="base",
                display_name="基线策略",
                version="1.0.0",
                frequency="daily",
                config_json=dict(_BASELINE_CONFIG),
            ),
            SimpleNamespace(strategy_id="dup"),  # 候选已存在
        ]
        svc._config_svc.get_parsed_config.return_value = MagicMock(portfolio=MagicMock())
        svc._config_svc.validate_config.return_value = StrategyValidationResult(
            valid=True, errors=[]
        )
        with pytest.raises(ValueError, match="已存在"):
            svc.start(
                "base",
                dict(_CANDIDATE_CONFIG),
                "假设",
                date(2024, 1, 1),
                date(2025, 1, 1),
            )


class TestEvaluate:
    """评估会话。"""

    def test_sync_runs_backtests_and_summarizes(self) -> None:
        """同步评估运行回测并落库逐折指标与汇总。"""
        svc = _make_service()
        session = _make_session(status="running")
        svc._repo = MagicMock()
        svc._repo.find_by_id.return_value = session

        def _apply_update(optimization_id: str, **fields: object) -> bool:
            for key, value in fields.items():
                setattr(session, key, value)
            return True

        svc._repo.update.side_effect = _apply_update
        svc._index_bar_repo = MagicMock()
        svc._index_bar_repo.find_all_trading_dates.return_value = [
            date(2024, 1, i + 1) for i in range(8)
        ]

        ids = iter(["bt_1", "bt_2", "bt_3", "bt_4", "bt_5", "bt_6"])

        def _fake_run(
            strategy_id: str,
            start: date,
            end: date,
            optimization_id: str,
            async_mode: bool,
        ) -> str:
            return next(ids)

        svc._run_backtest = MagicMock(side_effect=_fake_run)

        metrics = {
            "annualized_return_pct": 10.0,
            "cumulative_return_pct": 8.0,
            "max_drawdown_pct": -12.0,
            "sharpe_ratio": 1.2,
        }

        def _find(backtest_id: str) -> SimpleNamespace | None:
            return SimpleNamespace(status="success", metrics=dict(metrics))

        svc._backtest_repo = MagicMock()
        svc._backtest_repo.find_by_id.side_effect = _find

        result = svc.evaluate("opt1", folds=2)

        assert result["status"] == "evaluated"
        assert result["folds"] == [
            {"start": "2024-01-01", "end": "2024-01-04"},
            {"start": "2024-01-05", "end": "2024-01-08"},
        ]
        assert len(result["fold_backtests"]) == 2
        summary = result["fold_summary"]
        assert summary["total_folds"] == 2
        assert summary["metrics"]["sharpe_ratio"]["candidate_wins"] == 0

    def test_async_mode_enqueues_without_summary(self) -> None:
        """异步评估只登记回测 ID，不计算汇总。"""
        svc = _make_service()
        session = _make_session(status="running")
        svc._repo = MagicMock()
        svc._repo.find_by_id.return_value = session

        def _apply_update(optimization_id: str, **fields: object) -> bool:
            for key, value in fields.items():
                setattr(session, key, value)
            return True

        svc._repo.update.side_effect = _apply_update
        svc._index_bar_repo = MagicMock()
        svc._index_bar_repo.find_all_trading_dates.return_value = [
            date(2024, 1, i + 1) for i in range(8)
        ]
        svc._run_backtest = MagicMock(
            side_effect=lambda *a, **k: f"bt_{len(svc._run_backtest.call_args_list)}"
        )

        result = svc.evaluate("opt1", folds=2, async_mode=True)

        assert result["status"] == "running"
        assert result["fold_summary"] is None
        assert result["baseline_backtest_id"].startswith("bt_")


class TestFoldSummary:
    """逐折聚合统计。"""

    def test_mean_median_and_wins(self) -> None:
        """均值/中位数与候选胜出折数计算。"""
        folds = [
            {
                "fold": 0,
                "baseline": {"sharpe_ratio": 1.0, "max_drawdown_pct": -10.0},
                "candidate": {"sharpe_ratio": 1.2, "max_drawdown_pct": -12.0},
            },
            {
                "fold": 1,
                "baseline": {"sharpe_ratio": 1.1, "max_drawdown_pct": -9.0},
                "candidate": {"sharpe_ratio": 1.0, "max_drawdown_pct": -8.0},
            },
            {
                "fold": 2,
                "baseline": {"sharpe_ratio": None, "max_drawdown_pct": -11.0},
                "candidate": {"sharpe_ratio": 1.5, "max_drawdown_pct": -13.0},
            },
        ]
        summary = _compute_fold_summary(folds)
        sharpe = summary["metrics"]["sharpe_ratio"]
        assert sharpe["baseline_mean"] == pytest.approx(1.05)
        assert sharpe["candidate_mean"] == pytest.approx((1.2 + 1.0 + 1.5) / 3)
        assert sharpe["candidate_wins"] == 1
        assert sharpe["total_folds"] == 3


class TestFinish:
    """结束会话。"""

    def test_accept_promotes_candidate_config(self) -> None:
        """accept + promote 把候选配置写回基线并更新版本。"""
        svc = _make_service()
        session = _make_session(
            fold_summary={
                "metrics": {
                    "sharpe_ratio": {
                        "baseline_mean": 1.0,
                        "candidate_mean": 1.5,
                        "candidate_wins": 3,
                        "total_folds": 4,
                    },
                    "max_drawdown_pct": {
                        "baseline_mean": -10.0,
                        "candidate_mean": -9.0,
                    },
                    "cumulative_return_pct": {
                        "baseline_mean": 5.0,
                        "candidate_mean": 8.0,
                    },
                }
            }
        )
        svc._repo = MagicMock()
        svc._repo.find_by_id.return_value = session

        def _apply_update(optimization_id: str, **fields: object) -> bool:
            for key, value in fields.items():
                setattr(session, key, value)
            return True

        svc._repo.update.side_effect = _apply_update
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.return_value = SimpleNamespace(
            config_json=dict(_CANDIDATE_CONFIG)
        )

        result = svc.finish(
            "opt1",
            "accept",
            report_text="# 报告",
            promote=True,
        )

        assert result["status"] == "accepted"
        assert result["report"] == "# 报告"
        update_req = svc._config_svc.update_config.call_args.args[1]
        assert update_req.config_json == _CANDIDATE_CONFIG
        assert update_req.version == "1.0.1"

    def test_strict_accept_rejects_bad_candidate(self) -> None:
        """严格模式夏普不及基线时拒绝接受。"""
        svc = _make_service()
        session = _make_session(
            fold_summary={
                "metrics": {
                    "sharpe_ratio": {
                        "baseline_mean": 1.5,
                        "candidate_mean": 1.0,
                        "candidate_wins": 1,
                        "total_folds": 4,
                    },
                    "max_drawdown_pct": {
                        "baseline_mean": -10.0,
                        "candidate_mean": -9.0,
                    },
                    "cumulative_return_pct": {
                        "baseline_mean": 5.0,
                        "candidate_mean": 8.0,
                    },
                }
            }
        )
        svc._repo = MagicMock()
        svc._repo.find_by_id.return_value = session
        with pytest.raises(ValueError, match="严格验收未通过"):
            svc.finish("opt1", "accept", strict=True)
