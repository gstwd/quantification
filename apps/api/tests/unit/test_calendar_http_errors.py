"""交易日历不可用时的 HTTP 语义（C1）。

严格口径下日历不可用必须显式报错，不能返回近似日期或空数组：
- ``GET /system/data-quality`` → 503
- ``GET /ai-factors/previous-trading-day`` → 503
"""

from __future__ import annotations

from datetime import datetime, timezone

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

    def test_data_quality_returns_503(self, client: TestClient, monkeypatch) -> None:
        """数据质量总览在日历不可用时返回 503 而非 500/空结果。"""
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.IngestService.check_data_freshness",
            _raise_unavailable,
        )
        response = client.get("/api/system/data-quality")
        assert response.status_code == 503
        assert "交易日历不可用" in response.json()["detail"]

    def test_previous_trading_day_returns_503(self, client: TestClient, monkeypatch) -> None:
        """前一交易日查询在日历不可用时返回 503。"""
        monkeypatch.setattr(
            "quant_etf_api.infra.trading_calendar.TradingCalendar.latest_trading_day",
            _raise_unavailable,
        )
        response = client.get("/api/ai-factors/previous-trading-day")
        assert response.status_code == 503
        assert "交易日历不可用" in response.json()["detail"]


class TestCalendarAvailablePath:
    """日历可用时接口正常返回。"""

    def test_data_quality_ok(self, client: TestClient, monkeypatch) -> None:
        """日历正常时数据质量接口返回 200（服务被替换为桩返回值）。"""
        empty_group = {"total": 0, "up_to_date": 0, "stale": [], "missing": []}
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.IngestService.check_data_freshness",
            lambda self: {
                "index_bars": empty_group,
                "index_valuation": empty_group,
                "checked_at": datetime(2025, 1, 2, tzinfo=timezone.utc),
            },
        )
        response = client.get("/api/system/data-quality")
        assert response.status_code == 200
        assert isinstance(response.json(), dict)
