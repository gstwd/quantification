"""研究数据瘦身相关 HTTP 语义：预览只读、分批执行、410 与 409。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from quant_etf_api.main import app
from quant_etf_api.schemas.backtest import BacktestDetailSlimResult
from quant_etf_api.services.backtest_service import IndexDetailPrunedError


@pytest.fixture()
def client() -> TestClient:
    """构造测试客户端（不使用 with，避免触发 lifespan 副作用）。"""
    return TestClient(app, raise_server_exceptions=False)


def _result(dry_run: bool) -> BacktestDetailSlimResult:
    """构造瘦身结果。"""
    return BacktestDetailSlimResult(
        scope="lineage",
        scope_id="base",
        lineage_id="base",
        strategy_count=1,
        dry_run=dry_run,
        total_backtests=3,
        already_pruned=0,
        eligible_backtests=2,
        remaining_backtests=2,
    )


@pytest.fixture()
def fake_retention(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """替换路由使用的 RetentionService。"""
    fake = MagicMock()
    monkeypatch.setattr("quant_etf_api.api.routers.strategies.RetentionService", lambda db: fake)
    return fake


class TestResearchSlimRoutes:
    """策略研究数据瘦身路由。"""

    def test_preview_is_dry_run(self, client: TestClient, fake_retention: MagicMock) -> None:
        """GET 预览以 dry_run=True 调用服务。"""
        fake_retention.slim_backtest_details.return_value = _result(True)
        response = client.get("/api/strategies/base/research-slim")
        assert response.status_code == 200
        assert response.json()["eligible_backtests"] == 2
        assert fake_retention.slim_backtest_details.call_args.kwargs == {
            "strategy_id": "base",
            "dry_run": True,
        }

    def test_apply_passes_batch_size(self, client: TestClient, fake_retention: MagicMock) -> None:
        """POST 执行一批，max_backtests 透传。"""
        fake_retention.slim_backtest_details.return_value = _result(False)
        response = client.post("/api/strategies/base/research-slim", json={"max_backtests": 5})
        assert response.status_code == 200
        kwargs = fake_retention.slim_backtest_details.call_args.kwargs
        assert kwargs["dry_run"] is False and kwargs["max_backtests"] == 5

    def test_apply_validates_batch_size(
        self, client: TestClient, fake_retention: MagicMock
    ) -> None:
        """批量大小越界返回 422。"""
        response = client.post("/api/strategies/base/research-slim", json={"max_backtests": 0})
        assert response.status_code == 422
        fake_retention.slim_backtest_details.assert_not_called()

    def test_missing_strategy_404(self, client: TestClient, fake_retention: MagicMock) -> None:
        """策略不存在返回 404。"""
        fake_retention.slim_backtest_details.return_value = None
        assert client.get("/api/strategies/nope/research-slim").status_code == 404

    def test_legacy_research_data_route_removed(self, client: TestClient) -> None:
        """会删除台账证据的旧清理路由已移除。"""
        paths = app.openapi()["paths"]
        assert "/api/strategies/{strategy_id}/research-data" not in paths
        assert "delete" not in paths.get("/api/strategies/{strategy_id}/research-slim", {})


class TestPrunedDetailHttp:
    """逐指数明细已瘦身时返回 410。"""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get_attribution", "/api/backtests/bt1/attribution"),
            ("get_index_results", "/api/backtests/bt1/index-results"),
        ],
    )
    def test_returns_410(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str, path: str
    ) -> None:
        """服务抛 IndexDetailPrunedError → 410 且带说明。"""

        def _raise(*args: object, **kwargs: object) -> None:
            raise IndexDetailPrunedError("bt1", datetime(2026, 11, 6, tzinfo=timezone.utc), "归因")

        monkeypatch.setattr(
            f"quant_etf_api.services.backtest_service.BacktestService.{method}", _raise
        )
        response = client.get(path)
        assert response.status_code == 410
        assert "瘦身清理" in response.json()["detail"]


class TestStrategyDeleteHttp:
    """删除策略的保护映射为 409。"""

    def test_guard_returns_409(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """服务拒绝删除（ValueError）→ 409。"""

        def _raise(*args: object, **kwargs: object) -> bool:
            raise ValueError("策略 base 已产生 3 次试验")

        monkeypatch.setattr(
            "quant_etf_api.services.strategy_service.StrategyService.delete_config", _raise
        )
        response = client.delete("/api/strategies/base")
        assert response.status_code == 409
        assert "试验" in response.json()["detail"]
