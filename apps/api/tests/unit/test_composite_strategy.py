"""组合策略测试：配置规则、持仓级合成记账、组合回测编排与实时合成。"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from pydantic import ValidationError

from quant_etf_api.domain.portfolio.composite import MemberDay, blend_members
from quant_etf_api.domain.portfolio.execution import DayExecution
from quant_etf_api.engine.config import CompositeStrategyConfig, is_composite_config
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import StrategyConfigService

_DAYS = [date(2025, 3, 3) + timedelta(days=i) for i in range(4)]


def _composite(**fields: Any) -> CompositeStrategyConfig:
    """两成员 6:4 组合。"""
    base: dict[str, Any] = {
        "strategy_id": "combo",
        "display_name": "combo",
        "members": [{"strategy_id": "a", "weight": 0.6}, {"strategy_id": "b", "weight": 0.4}],
    }
    base.update(fields)
    return CompositeStrategyConfig(**base)


class TestConfig:
    """组合配置规则。"""

    def test_weights_must_sum_to_one(self) -> None:
        """成员权重 + 现金 = 1；重复成员与包含自身被拒绝。"""
        assert _composite().target_weights == {"a": 0.6, "b": 0.4}
        _composite(
            members=[{"strategy_id": "a", "weight": 0.5}, {"strategy_id": "b", "weight": 0.3}],
            cash_weight=0.2,
        )
        with pytest.raises(ValidationError):
            _composite(members=[{"strategy_id": "a", "weight": 0.5}])
        with pytest.raises(ValidationError):
            _composite(
                members=[{"strategy_id": "a", "weight": 0.5}, {"strategy_id": "a", "weight": 0.5}]
            )
        with pytest.raises(ValidationError):
            _composite(
                members=[
                    {"strategy_id": "combo", "weight": 0.5},
                    {"strategy_id": "b", "weight": 0.5},
                ]
            )

    def test_kind_detection(self) -> None:
        """kind=composite 判定。"""
        assert is_composite_config({"kind": "composite"})
        assert not is_composite_config({"score": {}})
        assert not is_composite_config(None)


def _day(ret: float, pre: dict[str, float], post: dict[str, float]) -> MemberDay:
    return MemberDay(portfolio_return=ret, pre_trade=pre, post_trade=post)


class TestBlend:
    """持仓级合成记账。"""

    def test_offsetting_trades_are_netted(self) -> None:
        """成员 A 卖 X 买 Y、成员 B 卖 Y 买 X，等权合成后无换手。"""
        days = {
            "a": {_DAYS[0]: _day(0.0, {"X": 1.0}, {"Y": 1.0})},
            "b": {_DAYS[0]: _day(0.0, {"Y": 1.0}, {"X": 1.0})},
        }
        result = blend_members(_DAYS[:1], days, {"a": 0.5, "b": 0.5})
        assert result[0].turnover == pytest.approx(0.0)
        assert result[0].positions == {"X": 0.5, "Y": 0.5}

    def test_single_member_matches_member(self) -> None:
        """单成员权重 1：收益与换手和成员自身一致。"""
        days = {
            "a": {
                _DAYS[0]: _day(1.0, {}, {"X": 0.8}),
                _DAYS[1]: _day(-2.0, {"X": 0.81}, {"X": 0.5, "Y": 0.3}),
            }
        }
        result = blend_members(_DAYS[:2], days, {"a": 1.0})
        assert [r.portfolio_return for r in result] == [1.0, -2.0]
        assert result[0].turnover == pytest.approx(0.4)
        assert result[1].turnover == pytest.approx((0.31 + 0.3) / 2)

    def test_member_weights_drift_between_rebalances(self) -> None:
        """非再平衡日成员权重随净值漂移，再平衡日调回目标。"""
        flat = {"X": 1.0}
        days = {
            "a": {d: _day(10.0, flat, flat) for d in _DAYS[:3]},
            "b": {d: _day(0.0, {"Y": 1.0}, {"Y": 1.0}) for d in _DAYS[:3]},
        }
        result = blend_members(_DAYS[:3], days, {"a": 0.5, "b": 0.5}, rebalance_dates={_DAYS[2]})
        assert result[0].member_weights == {"a": 0.5, "b": 0.5}
        assert result[0].portfolio_return == pytest.approx(5.0)
        assert result[1].member_weights["a"] == pytest.approx(0.55 / 1.05)
        assert result[1].turnover == pytest.approx(0.0)
        assert result[2].member_weights == {"a": 0.5, "b": 0.5}
        assert result[2].turnover > 0


def _snapshot() -> dict[str, Any]:
    """含两个成员快照的组合快照。"""
    member = {
        "display_name": "m",
        "version": "1.0.0",
        "frequency": "daily",
        "config_json": {"score": {"factors": {"val": 1.0}}, "index_codes": ["X"]},
        "config_hash": "h",
    }
    composite = _composite()
    return {
        "strategy_id": "combo",
        "display_name": "combo",
        "version": "1.0.0",
        "frequency": "daily",
        "config_json": composite.model_dump(
            exclude={"strategy_id", "display_name", "version", "description", "frequency"}
        ),
        "members": {"a": {**member, "strategy_id": "a"}, "b": {**member, "strategy_id": "b"}},
    }


class TestCompositeBacktest:
    """组合回测编排：逐成员内存回测 → 合成 → 落库。"""

    def test_snapshot_round_trip(self) -> None:
        """快照可还原为组合配置与成员配置；普通快照解析器对组合快照返回 None。"""
        parsed = StrategyConfigService.parse_composite_snapshot(_snapshot())
        assert parsed is not None
        composite, members = parsed
        assert composite.target_weights == {"a": 0.6, "b": 0.4}
        assert set(members) == {"a", "b"}
        assert StrategyConfigService.parse_snapshot(_snapshot()) is None

    def test_run_blends_members_and_marks_success(self) -> None:
        """成员结果按 6:4 合成为组合逐日收益，并写入组合口径指纹。"""
        svc = BacktestService(db=mock.MagicMock())
        svc._backtest_repo = mock.MagicMock()
        svc._load_all_index_bars = mock.MagicMock(return_value={})

        def _member_loop(backtest_id: str, row: Any, config: Any, persist: bool) -> dict[str, Any]:
            ret = 1.0 if row.strategy_id == "a" else -1.0
            execution = DayExecution(
                portfolio_return=ret,
                positions={"X": 1.0},
                executed_positions={"X": 1.0},
                turnover=0.0,
                missing_bar_count=0,
                pre_trade_positions={"X": 1.0},
            )
            return {
                "executions": [(d, execution) for d in _DAYS[:3]],
                "trading_dates": _DAYS,
                "metrics": {"sharpe_ratio": 1.0},
                "warnings": [{"code": "W", "message": "m"}],
            }

        svc._run_backtest_loop = mock.MagicMock(side_effect=_member_loop)
        row = BacktestRunModel(
            backtest_id="bt-c",
            strategy_id="combo",
            start_date=_DAYS[0],
            end_date=_DAYS[-1],
            universe_filter={"mode": "all"},
            params={"_execution_model": "t_plus_1_close", "_enable_benchmark": False},
            config_snapshot=_snapshot(),
            status="running",
        )
        outcome = svc._run_composite_backtest("bt-c", row)
        assert [d.portfolio_return for d in outcome["days"]] == [0.2, 0.2, 0.2]
        assert svc._backtest_repo.add_daily_result.call_count == 3
        assert row.params["_composite_model"] == "position_blend_v1"
        metrics = svc._backtest_repo.mark_success.call_args.args[1]
        assert [m["strategy_id"] for m in metrics["composite"]["members"]] == ["a", "b"]
        warnings = svc._backtest_repo.mark_success.call_args.kwargs["warnings"]
        assert warnings[0]["message"].startswith("[成员 a]")


class TestCompositeLive:
    """实时分配：成员各自决策后加权合成。"""

    def test_members_combined_by_weight(self) -> None:
        """组合持仓 = Σ 成员权重 × 成员持仓。"""
        from quant_etf_api.services.strategy_decision_service import StrategyDecisionService

        svc = StrategyDecisionService(mock.MagicMock())
        responses = {
            "a": SimpleNamespace(plan={"positions": {"X": 0.5}}, warnings=[], data_date=_DAYS[1]),
            "b": SimpleNamespace(
                plan={"positions": {"X": 0.5, "Y": 0.5}}, warnings=[], data_date=_DAYS[0]
            ),
        }
        svc.run_allocation = mock.MagicMock(side_effect=lambda sid, _d: responses[sid])
        result = svc._composite_allocation(_composite(), None)
        assert result.plan["positions"] == {"X": 0.5, "Y": 0.2}
        assert result.plan["method"] == "composite"
        assert result.data_date == _DAYS[0]
