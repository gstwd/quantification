"""研究期边界与上线冻结的守卫测试（越界拒绝 / LIVE 禁止改配置 / 上线冻结）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.schemas.lifecycle import LifecycleOnlineRequest, LifecycleStatusRequest
from quant_etf_api.schemas.strategy import StrategyConfigUpdate
from quant_etf_api.services import backtest_service as backtest_module
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.optimization_service import OptimizationService
from quant_etf_api.services.strategy_config_service import StrategyConfigService
from quant_etf_api.services.strategy_lifecycle_service import StrategyLifecycleService


def _mocked_backtest_service(monkeypatch) -> BacktestService:
    """构造配置服务被替换为 Mock 的 BacktestService。"""
    monkeypatch.setattr(backtest_module, "StrategyConfigService", lambda db: MagicMock())
    return BacktestService(db=MagicMock())


class TestBacktestPeriodGuard:
    """回测创建时的用途边界守卫。"""

    def test_research_purpose_crossing_boundary_rejected(self, monkeypatch) -> None:
        """研究类回测越过研究期末端时直接拒绝。"""
        svc = _mocked_backtest_service(monkeypatch)
        req = BacktestCreateRequest(
            strategy_id="s1", start_date=date(2020, 1, 1), end_date=date(2026, 3, 1)
        )
        with pytest.raises(ValueError) as exc:
            svc.create_backtest(req)
        assert "研究期末端" in str(exc.value)

    def test_validation_purpose_allows_validation_window(self, monkeypatch) -> None:
        """验证类回测允许使用验证期数据，并原样记录用途。"""
        svc = _mocked_backtest_service(monkeypatch)
        req = BacktestCreateRequest(
            strategy_id="s1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 9, 13),
            purpose="validation",
            purpose_reason="单元测试",
        )
        summary = svc.create_backtest(req)
        assert summary.purpose == "validation"

    def test_cost_bps_defaults_from_settings(self, monkeypatch) -> None:
        """未显式给出成本时取系统默认值，并随回测固化。"""
        svc = _mocked_backtest_service(monkeypatch)
        req = BacktestCreateRequest(
            strategy_id="s1", start_date=date(2020, 1, 1), end_date=date(2020, 12, 31)
        )
        svc.create_backtest(req)
        row = svc._db.add.call_args[0][0]
        assert row.params["_cost_bps"] == pytest.approx(0.5)
        assert row.purpose == "research"


class TestOptimizationPeriodGuard:
    """优化会话同样不得触碰验证期数据。"""

    def test_optimization_crossing_boundary_rejected(self) -> None:
        """优化评估区间越过研究期末端时拒绝。"""
        svc = OptimizationService(db=MagicMock())
        with pytest.raises(ValueError) as exc:
            svc.start(
                strategy_id="base",
                candidate_config={"score": {"factors": {"return_20d": 1.0}}},
                hypothesis="测试假设",
                start_date=date(2024, 1, 1),
                end_date=date(2026, 6, 30),
            )
        assert "研究期末端" in str(exc.value)


class TestLiveFreeze:
    """LIVE 状态下的配置冻结。"""

    def test_live_strategy_config_update_rejected(self) -> None:
        """策略处于 LIVE 时禁止直接修改 config_json。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.one_or_none.return_value = SimpleNamespace(
            strategy_id="s1", lifecycle_status="LIVE"
        )
        svc = StrategyConfigService(db)
        svc._repo = MagicMock()
        svc._repo.find_by_id.return_value = SimpleNamespace(strategy_id="s1")
        with pytest.raises(ValueError) as exc:
            svc.update_config("s1", StrategyConfigUpdate(config_json={"score": {}}))
        assert "已冻结" in str(exc.value)

    def test_suspended_strategy_allows_update(self) -> None:
        """暂停观察（SUSPENDED）后允许修改配置。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.one_or_none.return_value = None
        svc = StrategyConfigService(db)
        svc._repo = MagicMock()
        existing = SimpleNamespace(
            strategy_id="s1",
            display_name="策略",
            version="1.0.0",
            frequency="daily",
            description="",
            status="active",
            is_starred=False,
            config_json={},
        )
        svc._repo.find_by_id.return_value = existing
        svc.validate_config = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(valid=True, errors=[])
        )
        svc.get_config = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
        result = svc.update_config("s1", StrategyConfigUpdate(config_json={"score": {}}))
        assert result is not None
        assert existing.config_json == {"score": {}, "schema_version": "2"}


class TestLifecycleOnline:
    """上线冻结流程。"""

    def test_online_freezes_snapshot_and_distribution(self) -> None:
        """上线时冻结配置快照、生成研究期分布，并写入生命周期行。"""
        svc = StrategyLifecycleService(db=MagicMock())
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.return_value = SimpleNamespace(
            strategy_id="s1",
            display_name="策略一",
            version="1.0.0",
            frequency="daily",
            config_json={"score": {"factors": {"return_20d": 1.0}}},
        )
        # 复用已有研究期回测，避免在单测里真的跑十年回测
        svc._find_reusable_research_backtest = MagicMock(return_value="bt-research")  # type: ignore[method-assign]
        svc._build_distribution = MagicMock(  # type: ignore[method-assign]
            return_value={"sample_days": 2400, "windows": {"3m": {"days": 63}}}
        )
        svc._find = MagicMock(return_value=None)  # type: ignore[method-assign]
        stored: dict[str, object] = {}

        def _capture(row: object) -> None:
            stored["row"] = row

        svc._db.add.side_effect = _capture
        svc.get_lifecycle = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]

        svc.online("s1", LifecycleOnlineRequest(live_at=date(2026, 1, 5), note="研究通过"))

        row = stored["row"]
        assert row.frozen_config_hash
        assert row.frozen_config_snapshot["version"] == "1.0.0"
        assert row.research_backtest_id == "bt-research"
        assert row.baseline_distribution["sample_days"] == 2400
        assert row.lifecycle_status == "LIVE"

    def test_online_rejects_date_before_validation_period(self) -> None:
        """上线日期早于验证期起点时拒绝（研究期不能作为监控起点）。"""
        svc = StrategyLifecycleService(db=MagicMock())
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.return_value = SimpleNamespace(
            strategy_id="s1",
            display_name="策略一",
            version="1.0.0",
            frequency="daily",
            config_json={},
        )
        with pytest.raises(ValueError) as exc:
            svc.online("s1", LifecycleOnlineRequest(live_at=date(2025, 6, 1)))
        assert "验证期起点" in str(exc.value)


class TestLifecycleRestore:
    """恢复 LIVE 前必须确认策略仍是上线时冻结的版本。"""

    def test_restore_live_rejects_changed_config(self) -> None:
        svc = StrategyLifecycleService(db=MagicMock())
        svc._find = MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                strategy_id="s1",
                lifecycle_status="SUSPENDED",
                frozen_config_hash="different",
            )
        )
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.return_value = SimpleNamespace(config_json={"score": {}})
        with pytest.raises(ValueError) as exc:
            svc.update_status("s1", LifecycleStatusRequest(status="LIVE"))
        assert "冻结版本" in str(exc.value)
