"""交易日历严格口径（C1）单元测试。

覆盖：
- 上游数据源 → 本地 trading_calendar 表 → 报错的解析优先级；
- 日历不可用时所有查询方法抛 ``TradingCalendarUnavailableError``（不再按星期近似）；
- 负缓存 60 秒（避免一次瞬时故障冻结日历一整天）；
- ``WeekendFallbackCalendar`` 已彻底删除。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import quant_etf_api.infra.trading_calendar as calendar_module
from quant_etf_api.domain.common.trading_calendar import (
    TradingCalendarUnavailableError,
)
from quant_etf_api.infra.trading_calendar import (
    DbTradingCalendar,
    TradingCalendar,
    load_db_trading_days,
    resolve_trading_calendar,
)

DAYS = {date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)}


@pytest.fixture(autouse=True)
def _reset_calendar_cache() -> None:
    """每个用例前后清空进程级日历缓存，避免用例间互相污染。"""
    calendar_module._cache["trading_days"] = None
    calendar_module._cache["loaded_at"] = None
    calendar_module._cache["source"] = None
    yield
    calendar_module._cache["trading_days"] = None
    calendar_module._cache["loaded_at"] = None
    calendar_module._cache["source"] = None


class TestWeekendFallbackRemoved:
    """降级实现必须彻底删除。"""

    def test_no_weekend_fallback_class(self) -> None:
        """模块内不得再存在 WeekendFallbackCalendar。"""
        assert not hasattr(calendar_module, "WeekendFallbackCalendar")

    def test_domain_module_has_no_fallback(self) -> None:
        """领域层同样不再提供周末兜底实现。"""
        from quant_etf_api.domain.common import trading_calendar as domain_module

        assert not hasattr(domain_module, "WeekendFallbackCalendar")


class TestStrictQueriesRaise:
    """上游不可用时所有查询方法抛错。"""

    def test_is_trading_day_raises(self, monkeypatch) -> None:
        """is_trading_day 在日历不可用时抛错。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (None, None))
        with pytest.raises(TradingCalendarUnavailableError):
            TradingCalendar().is_trading_day(date(2024, 1, 2))

    def test_latest_trading_day_raises(self, monkeypatch) -> None:
        """latest_trading_day 在日历不可用时抛错。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (None, None))
        with pytest.raises(TradingCalendarUnavailableError):
            TradingCalendar().latest_trading_day(date(2024, 1, 5))

    def test_next_trading_day_raises(self, monkeypatch) -> None:
        """next_trading_day 在日历不可用时抛错。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (None, None))
        with pytest.raises(TradingCalendarUnavailableError):
            TradingCalendar().next_trading_day(date(2024, 1, 5))

    def test_trading_days_between_raises(self, monkeypatch) -> None:
        """trading_days_between 在日历不可用时抛错。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (None, None))
        with pytest.raises(TradingCalendarUnavailableError):
            TradingCalendar().trading_days_between(date(2024, 1, 1), date(2024, 1, 31))

    def test_get_trading_days_set_returns_none(self, monkeypatch) -> None:
        """探测 API 保持返回 None（不抛错），供解析器判定上游可用性。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (None, None))
        assert TradingCalendar().get_trading_days_set() is None

    def test_error_message_has_repair_hint(self, monkeypatch) -> None:
        """报错信息包含修复指引，便于运维定位。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (None, None))
        with pytest.raises(TradingCalendarUnavailableError) as exc:
            TradingCalendar().is_trading_day(date(2024, 1, 2))
        assert "交易日历不可用" in str(exc.value)
        assert "数据管理" in str(exc.value)

    def test_successful_upstream_records_source(self, monkeypatch) -> None:
        """成功加载时记录来源并正常返回。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (DAYS, "akshare"))
        cal = TradingCalendar()
        assert cal.is_trading_day(date(2024, 1, 3)) is True
        assert cal.is_trading_day(date(2024, 1, 6)) is False
        assert cal.latest_trading_day(date(2024, 1, 6)) == date(2024, 1, 4)
        assert cal.next_trading_day(date(2024, 1, 2)) == date(2024, 1, 3)
        assert cal.trading_days_between(date(2024, 1, 1), date(2024, 1, 3)) == [
            date(2024, 1, 2),
            date(2024, 1, 3),
        ]
        assert cal.source == "akshare"


class TestNegativeCache:
    """失败结果只做短负缓存。"""

    def test_negative_result_not_cached_for_a_day(self, monkeypatch) -> None:
        """负缓存 60 秒内不重试，超过后重试上游（避免瞬时故障冻结整天）。"""
        calls: list[int] = []

        def _loader() -> tuple[set[date] | None, str | None]:
            calls.append(1)
            return None, None

        monkeypatch.setattr(calendar_module, "_load_from_upstream", _loader)
        cal = TradingCalendar()
        assert cal.get_trading_days_set() is None
        assert cal.get_trading_days_set() is None
        assert len(calls) == 1, "60 秒内应命中负缓存"

        # 把加载时间人为推早，模拟负缓存过期
        stale_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=61)
        calendar_module._cache["loaded_at"] = stale_at
        assert cal.get_trading_days_set() is None
        assert len(calls) == 2, "负缓存过期后应重试上游"


class TestDbTradingCalendar:
    """本地日历表快照实现。"""

    def test_is_trading_day_and_latest(self) -> None:
        """按本地交易日集合判断与回溯。"""
        cal = DbTradingCalendar(DAYS)
        assert cal.is_trading_day(date(2024, 1, 3)) is True
        assert cal.is_trading_day(date(2024, 1, 6)) is False
        assert cal.latest_trading_day(date(2024, 1, 6)) == date(2024, 1, 4)
        assert cal.source == "database"
        assert cal.coverage() == (date(2024, 1, 2), date(2024, 1, 4))

    def test_empty_days_rejected(self) -> None:
        """空集合不允许构建（避免"看似可用实则无日历"）。"""
        with pytest.raises(ValueError):
            DbTradingCalendar(set())


class TestResolveTradingCalendar:
    """严格解析优先级。"""

    def test_prefers_upstream(self, monkeypatch) -> None:
        """上游可用时使用上游并返回 upstream。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (DAYS, "tushare"))
        cal, source = resolve_trading_calendar(MagicMock())
        assert source == "upstream"
        assert isinstance(cal, TradingCalendar)

    def test_falls_back_to_db_table(self, monkeypatch) -> None:
        """上游不可用时回退本地表快照，来源为 database。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (None, None))
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [
            (date(2024, 1, 2),),
            (date(2024, 1, 3),),
        ]
        cal, source = resolve_trading_calendar(db)
        assert source == "database"
        assert isinstance(cal, DbTradingCalendar)

    def test_raises_when_both_unavailable(self, monkeypatch) -> None:
        """上游与本地表都不可用时抛错（不回退周末判断）。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (None, None))
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        with pytest.raises(TradingCalendarUnavailableError):
            resolve_trading_calendar(db)

    def test_required_range_not_covered(self, monkeypatch) -> None:
        """本地快照未覆盖所需区间时视为不可用。"""
        monkeypatch.setattr(calendar_module, "_load_from_upstream", lambda: (None, None))
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [(date(2024, 1, 2),)]
        with pytest.raises(TradingCalendarUnavailableError):
            resolve_trading_calendar(db, required_range=(date(2016, 1, 1), date(2016, 12, 31)))

    def test_load_db_trading_days_handles_error(self) -> None:
        """读取本地表异常时返回 None（由调用方按不可用处理）。"""
        db = MagicMock()
        db.query.side_effect = RuntimeError("boom")
        assert load_db_trading_days(db) is None

    def test_load_db_trading_days_empty(self) -> None:
        """本地表为空时返回 None。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        assert load_db_trading_days(db) is None
