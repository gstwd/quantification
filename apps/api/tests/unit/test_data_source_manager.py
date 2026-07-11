"""DataSourceManager 单元测试。"""

import pytest

from quant_etf_api.infra.data_sources.base import (
    BaseDataSourceAdapter,
    DataSourceError,
    SourceCapability,
)
from quant_etf_api.infra.data_sources.circuit_breaker import CircuitBreaker
from quant_etf_api.infra.data_sources.manager import DataSourceManager


# ---- Mock Adapters ----


class _MockIndexAdapter(BaseDataSourceAdapter):
    """模拟指数数据源适配器。"""

    name = "mock_index"
    priority = 10

    def __init__(self, should_fail: bool = False):
        self._should_fail = should_fail
        self.fetch_calls: list[tuple] = []

    @property
    def is_available(self):
        return True

    @property
    def capabilities(self):
        return SourceCapability(supports_index_daily=True, markets=["cn"])

    def fetch_index_daily(self, code, start, end):
        self.fetch_calls.append((code, start, end))
        if self._should_fail:
            raise RuntimeError("模拟失败")
        return [{"trade_date": "2024-01-01", "close_price": 3500.0}]


class _MockFundAdapter(BaseDataSourceAdapter):
    """模拟 ETF 数据源适配器。"""

    name = "mock_fund"
    priority = 20

    @property
    def is_available(self):
        return True

    @property
    def capabilities(self):
        return SourceCapability(supports_etf_kline=True, markets=["cn"])

    def fetch_etf_daily_bars(self, code, start, end):
        return [{"trade_date": "2024-01-01", "close_price": 2.5}]

    def fetch_share_snapshot(self, code):
        return {"shares_total": 1000000}


class _MockUnavailableAdapter(BaseDataSourceAdapter):
    """模拟不可用的适配器。"""

    name = "mock_unavailable"
    priority = 5

    @property
    def is_available(self):
        return False

    @property
    def capabilities(self):
        return SourceCapability(supports_index_daily=True, markets=["cn"])


class _MockWrongMarketAdapter(BaseDataSourceAdapter):
    """模拟仅支持海外市场的适配器。"""

    name = "mock_us_only"
    priority = 5

    @property
    def is_available(self):
        return True

    @property
    def capabilities(self):
        return SourceCapability(supports_index_daily=True, markets=["us"])


# ---- Tests ----


class TestDataSourceManagerRegistration:
    """测试适配器注册。"""

    def test_register_sorts_by_priority(self):
        mgr = DataSourceManager()
        mgr.register(_MockFundAdapter())  # priority 20
        mgr.register(_MockIndexAdapter())  # priority 10

        names = [a.name for a in mgr._adapters]
        assert names == ["mock_index", "mock_fund"]  # 按优先级排序

    def test_available_sources(self):
        mgr = DataSourceManager()
        mgr.register(_MockIndexAdapter())
        mgr.register(_MockUnavailableAdapter())

        sources = mgr.available_sources
        assert "mock_index" in sources
        assert "mock_unavailable" not in sources


class TestDataSourceManagerRouting:
    """测试能力路由。"""

    def test_capability_filtering(self):
        mgr = DataSourceManager()
        mgr.register(_MockIndexAdapter())  # supports index_daily
        mgr.register(_MockFundAdapter())  # supports etf_kline

        # 查找 index_daily 能力
        index_adapters = mgr._find_adapters_for("index_daily", "cn")
        assert len(index_adapters) == 1
        assert index_adapters[0].name == "mock_index"

        # 查找 etf_kline 能力
        fund_adapters = mgr._find_adapters_for("etf_kline", "cn")
        assert len(fund_adapters) == 1
        assert fund_adapters[0].name == "mock_fund"

    def test_market_filtering(self):
        mgr = DataSourceManager()
        mgr.register(_MockWrongMarketAdapter())

        # CN 市场找不到 US-only 适配器
        adapters = mgr._find_adapters_for("index_daily", "cn")
        assert len(adapters) == 0

        # US 市场可以找到
        adapters = mgr._find_adapters_for("index_daily", "us")
        assert len(adapters) == 1

    def test_unavailable_filtered(self):
        mgr = DataSourceManager()
        mgr.register(_MockUnavailableAdapter())
        mgr.register(_MockIndexAdapter())

        adapters = mgr._find_adapters_for("index_daily", "cn")
        assert len(adapters) == 1
        assert adapters[0].name == "mock_index"


class TestDataSourceManagerFailover:
    """测试故障切换。"""

    def test_primary_succeeds(self):
        mgr = DataSourceManager()
        mgr.register(_MockIndexAdapter())
        result, source = mgr.fetch_index_daily("000300")
        assert len(result) == 1
        assert source == "mock_index"

    def test_failover_to_secondary(self):
        """主源失败时自动切换到备源。"""
        mgr = DataSourceManager()
        mgr.register(_MockIndexAdapter(should_fail=True))  # priority 10, 会失败
        good = _MockIndexAdapter(should_fail=False)
        good.name = "mock_index_backup"
        good.priority = 20
        mgr.register(good)

        result, source = mgr.fetch_index_daily("000300")
        assert len(result) == 1
        assert source == "mock_index_backup"

    def test_all_fail_raises_error(self):
        mgr = DataSourceManager()
        mgr.register(_MockIndexAdapter(should_fail=True))
        failing2 = _MockIndexAdapter(should_fail=True)
        failing2.name = "mock_index_2"
        failing2.priority = 20
        mgr.register(failing2)

        with pytest.raises(DataSourceError, match="所有数据源"):
            mgr.fetch_index_daily("000300")


class TestDataSourceManagerCircuitBreakerIntegration:
    """测试熔断器集成。"""

    def test_circuit_breaker_opens_on_repeated_failures(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=300)
        mgr = DataSourceManager(circuit_breaker=cb)

        failing = _MockIndexAdapter(should_fail=True)
        failing.name = "flaky"
        failing.priority = 10
        mgr.register(failing)

        # 第一次失败
        with pytest.raises(DataSourceError):
            mgr.fetch_index_daily("000300")
        assert cb.get_status("index_daily:cn:flaky")["failures"] == 1

        # 第二次失败（触发熔断）
        with pytest.raises(DataSourceError):
            mgr.fetch_index_daily("000300")
        status = cb.get_status("index_daily:cn:flaky")
        assert status["state"] == CircuitBreaker.OPEN


class TestDataSourceManagerGetStatus:
    """测试 get_source_status 方法。"""

    def test_status_report(self):
        mgr = DataSourceManager()
        mgr.register(_MockIndexAdapter())
        mgr.register(_MockFundAdapter())

        report = mgr.get_source_status()
        assert "mock_index" in report
        assert "mock_fund" in report
        assert report["mock_index"]["available"] is True
        assert report["mock_index"]["capabilities"]["index_daily"] is True
        assert report["mock_fund"]["capabilities"]["etf_kline"] is True


class TestDataSourceManagerFromSettings:
    """测试 from_settings 工厂方法。"""

    def test_creates_manager_with_defaults(self):
        mgr = DataSourceManager.from_settings(settings=None)
        # 应自动注册 4 个内置适配器
        names = [a.name for a in mgr._adapters]
        assert "akshare_fund" in names
        assert "akshare_index" in names
        assert "akshare_macro" in names
        assert "exchange_reference" in names
