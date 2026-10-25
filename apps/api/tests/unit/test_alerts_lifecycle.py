"""LIVE 策略定时体检与站内告警测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from quant_etf_api.services import strategy_lifecycle_service as module
from quant_etf_api.services.alert_service import is_degraded, severity_of_health
from quant_etf_api.services.strategy_lifecycle_service import StrategyLifecycleService


class TestHealthOrder:
    """健康等级恶化判定。"""

    @pytest.mark.parametrize(
        ("previous", "current", "expected"),
        [
            ("HEALTHY", "WATCH", True),
            ("WATCH", "CRITICAL", True),
            ("WARNING", "WATCH", False),
            ("HEALTHY", "HEALTHY", False),
            (None, "CRITICAL", False),
            ("HEALTHY", "UNKNOWN", False),
        ],
    )
    def test_is_degraded(self, previous: str | None, current: str, expected: bool) -> None:
        """只有已知等级变差才算恶化。"""
        assert is_degraded(previous, current) is expected

    def test_severity(self) -> None:
        """CRITICAL / WARNING 映射到同名严重程度，其余为 info。"""
        assert severity_of_health("CRITICAL") == "critical"
        assert severity_of_health("WATCH") == "info"


def _service(levels: dict[str, str | None]) -> StrategyLifecycleService:
    """MagicMock 依赖的生命周期服务：两条 LIVE 策略。"""
    svc = StrategyLifecycleService.__new__(StrategyLifecycleService)
    svc._db = mock.MagicMock()
    svc._db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(strategy_id=sid) for sid in levels
    ]
    svc._find = mock.MagicMock(  # type: ignore[method-assign]
        side_effect=lambda sid: SimpleNamespace(
            latest_health_level=levels[sid], validation_backtest_id=f"bt-{sid}"
        )
    )
    svc._prune_scheduled_backtests = mock.MagicMock(return_value=1)  # type: ignore[method-assign]
    return svc


class TestRefreshAllLive:
    """定时体检编排。"""

    def test_degradation_and_failure_alerts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """恶化写 health_degraded，失败写 lifecycle_refresh_failed，其余照常。"""
        raised: list[dict[str, Any]] = []

        class FakeAlerts:
            def __init__(self, _db: Any) -> None:
                pass

            def raise_alert(self, **kwargs: Any) -> bool:
                raised.append(kwargs)
                return True

        monkeypatch.setattr("quant_etf_api.services.alert_service.AlertService", FakeAlerts)
        svc = _service({"a": "HEALTHY", "b": "WATCH"})

        def _refresh(sid: str, _req: Any, trigger: str) -> Any:
            assert trigger == module.TRIGGER_SCHEDULED
            if sid == "b":
                raise ValueError("监控回测失败")
            return SimpleNamespace(
                health_level="WARNING",
                diagnosis="DRAWDOWN_EXTREME",
                recommended_action="REVIEW",
                reasons=["r"],
            )

        svc.refresh = mock.MagicMock(side_effect=_refresh)  # type: ignore[method-assign]
        summary = svc.refresh_all_live()
        assert summary["refreshed"] == ["a"] and summary["failed"] == ["b"]
        assert summary["degraded"] == ["a"] and summary["pruned"] == 1
        kinds = {item["kind"]: item for item in raised}
        assert kinds["health_degraded"]["severity"] == "warning"
        assert kinds["health_degraded"]["dedupe_key"].startswith("health_degraded:a:")
        assert kinds["lifecycle_refresh_failed"]["strategy_id"] == "b"


class TestPrune:
    """定时监控回测清理。"""

    def test_keeps_latest_and_protected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """只删除超出保留条数且未被生命周期引用的定时回测；引用冲突跳过。"""
        monkeypatch.setattr(
            module, "get_settings", lambda: SimpleNamespace(lifecycle_scheduled_keep=2)
        )
        svc = StrategyLifecycleService.__new__(StrategyLifecycleService)
        svc._db = mock.MagicMock()
        rows = [("n1",), ("n2",), ("old1",), ("bt-live",), ("old2",)]
        svc._db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows
        svc._find = mock.MagicMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(validation_backtest_id="bt-live")
        )
        svc._backtest_svc = mock.MagicMock()
        svc._backtest_svc.delete_backtest.side_effect = lambda bid: (
            (_ for _ in ()).throw(ValueError("引用")) if bid == "old2" else None
        )
        assert svc._prune_scheduled_backtests("s") == 1
        deleted = [call.args[0] for call in svc._backtest_svc.delete_backtest.call_args_list]
        assert deleted == ["old1", "old2"]
