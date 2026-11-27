"""交易日历不可用时的 HTTP 语义。

严格口径下日历不可用必须显式报错，不能返回近似日期或空数组：
- ``GET /market-data/previous-trading-day`` → 503

说明：原先的 ``GET /system/data-quality`` 同样返回 503，该端点已随质量口径
统一（改由数据管理页的 ``data_health_snapshot`` 承担）而删除，相应用例一并移除。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from quant_etf_api.domain.common.trading_calendar import (
    TradingCalendarUnavailableError,
)
from quant_etf_api.main import app


@pytest.fixture()
def client() -> TestClient:
    """构造测试客户端（不使用 with，避免触发 lifespan 副作用）。"""
    return TestClient(app, raise_server_exceptions=False)


def _raise_unavailable(*args: object, **kwargs: object) -> None:
    """抛日历不可用异常。"""
    raise TradingCalendarUnavailableError("交易日历不可用：Tushare 与 AkShare 均加载失败")


class TestCalendarUnavailableHttp:
    """503 语义。"""

    def test_previous_trading_day_returns_503(self, client: TestClient, monkeypatch) -> None:
        """前一交易日查询在日历不可用时返回 503。"""
        monkeypatch.setattr(
            "quant_etf_api.infra.trading_calendar.TradingCalendar.latest_trading_day",
            _raise_unavailable,
        )
        response = client.get("/api/market-data/previous-trading-day")
        assert response.status_code == 503
        assert "交易日历不可用" in response.json()["detail"]

    def test_previous_trading_day_returns_date(self, client: TestClient, monkeypatch) -> None:
        """交易日历可用时返回前一交易日。"""
        monkeypatch.setattr(
            "quant_etf_api.infra.trading_calendar.TradingCalendar.latest_trading_day",
            lambda *args, **kwargs: date(2026, 11, 27),
        )
        response = client.get("/api/market-data/previous-trading-day")
        assert response.status_code == 200
        assert response.json() == {"trade_date": "2026-11-27"}


class TestQualityCaliberEndpoints:
    """质量口径只来自数据管理页健康快照，没有单独的质量端点。"""

    def test_legacy_quality_endpoints_are_gone(self, client: TestClient) -> None:
        """``/system/data-quality`` 与单指数 data-quality 端点应返回 404。"""
        assert client.get("/api/system/data-quality").status_code == 404
        assert client.get("/api/market-data/indexes/000300/data-quality").status_code == 404

    def test_data_management_routes_registered(self) -> None:
        """数据管理口径端点（总览与数据集分区详情）应已注册。"""
        paths = set(app.openapi()["paths"])
        assert "/api/data-management" in paths
        assert "/api/data-management/datasets/{dataset_key}" in paths

    def test_ai_sentiment_routes_are_gone(self) -> None:
        """AI 舆情与关键词配置路由不再注册。"""
        paths = set(app.openapi()["paths"])
        assert not any(path.startswith("/api/ai-factors") for path in paths)
        assert not any(path.startswith("/api/keyword-tags") for path in paths)
