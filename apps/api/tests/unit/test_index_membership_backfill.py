"""测试指数 PIT 成分回填的“从新到旧、无数据即停”逻辑。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import quant_etf_api.services.index_membership_data_service as service_module
from quant_etf_api.services.index_membership_data_service import (
    IndexMembershipDataService,
)


class _FakeRepo:
    """捕获批量写入行的假仓库。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def bulk_upsert_pit(self, rows: list[dict]) -> int:
        """记录写入行。"""
        self.rows.extend(rows)
        return len(rows)


class _FakeTushare:
    """按日期返回成分的假 Tushare 客户端。"""

    def __init__(
        self,
        available: set[date] | None = None,
        fail_dates: set[date] | None = None,
    ) -> None:
        self.available = available or set()
        self.fail_dates = fail_dates or set()

    def is_configured(self) -> bool:
        """模拟已配置。"""
        return True

    def fetch_weight_snapshot(self, index_code: str, trade_date: date) -> list[dict]:
        """按预设可用日期返回。"""
        if trade_date in self.fail_dates:
            raise RuntimeError("接口限频")
        if trade_date not in self.available:
            return []
        return [{"stock_code": "600000", "weight": 0.05}]


class _FakeBaostock:
    """始终返回空（验证 Tushare 为主路径）。"""

    def fetch_pit(self, index_code: str, trade_date: date) -> list[dict]:
        """返回空。"""
        return []


class _RetryOnceTushare:
    """第一次返回空、重试即有数据的假 Tushare 客户端。"""

    def __init__(self, empty_once_dates: set[date]) -> None:
        self.empty_once_dates = empty_once_dates
        self.calls: list[date] = []

    def is_configured(self) -> bool:
        """模拟已配置。"""
        return True

    def fetch_weight_snapshot(self, index_code: str, trade_date: date) -> list[dict]:
        """首次空、第二次返回数据。"""
        self.calls.append(trade_date)
        if trade_date in self.empty_once_dates and self.calls.count(trade_date) == 1:
            return []
        return [{"stock_code": "600000", "weight": 0.05}]


class _FakeCalendar:
    """直接返回传入日作为交易日。"""

    def latest_trading_day(self, target: date) -> date:
        """返回目标日。"""
        return target


def _make_service(
    monkeypatch,
    tushare: _FakeTushare,
) -> tuple[IndexMembershipDataService, _FakeRepo]:
    """构造注入假依赖的服务实例。"""
    monkeypatch.setattr(service_module, "_EMPTY_RETRY_SLEEP", 0.0)
    db = MagicMock()
    service = IndexMembershipDataService(db)
    repo = _FakeRepo()
    service._repo = repo
    service._tushare = tushare
    service._baostock = _FakeBaostock()
    monkeypatch.setattr(
        service_module,
        "TradingCalendar",
        lambda: _FakeCalendar(),
    )
    return service, repo


def test_backfill_stops_at_first_empty_month(monkeypatch) -> None:
    """最新月有数据、更早无数据时只写最新月并正常返回。"""
    tushare = _FakeTushare(available={date(2026, 3, 31)})
    service, repo = _make_service(monkeypatch, tushare)
    result = service.backfill_pit(
        ["000300"],
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
    )
    assert result["errors"] == []
    assert result["items"] == {"000300": 1}
    assert len(repo.rows) == 1
    assert repo.rows[0]["start_date"] == date(2026, 3, 31)
    assert repo.rows[0]["end_date"] is None


def test_backfill_continues_after_error_month(monkeypatch) -> None:
    """接口失败月份记录错误并继续向更早月份回填，直到无数据月停止。"""
    tushare = _FakeTushare(
        available={date(2026, 2, 28)},
        fail_dates={date(2026, 3, 31)},
    )
    service, repo = _make_service(monkeypatch, tushare)
    result = service.backfill_pit(
        ["000300"],
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
    )
    assert len(result["errors"]) == 1
    assert result["items"] == {"000300": 1}
    assert repo.rows[0]["start_date"] == date(2026, 2, 28)
    assert repo.rows[0]["end_date"] == date(2026, 3, 30)


def test_backfill_retries_transient_empty(monkeypatch) -> None:
    """单月瞬时空返回触发一次重试，不把该月误判为历史起点。"""
    tushare = _RetryOnceTushare(empty_once_dates={date(2026, 3, 31)})
    service, repo = _make_service(monkeypatch, tushare)
    result = service.backfill_pit(
        ["000300"],
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
    )
    assert result["errors"] == []
    assert result["items"] == {"000300": 3}
    assert tushare.calls.count(date(2026, 3, 31)) == 2
    starts = {row["start_date"] for row in repo.rows}
    assert starts == {
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
    }
