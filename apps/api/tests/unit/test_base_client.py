"""测试 BaseDataClient 基类基本行为。"""

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus


class _ConcreteClient(BaseDataClient):
    """用于测试的最小子类。"""

    source_name = "test_client"


class _BrokenClient(BaseDataClient):
    source_name = "broken"

    def health_check(self) -> HealthStatus:
        raise ConnectionError("模拟的网络错误")


class TestBaseDataClient:
    """基类契约测试。"""

    def test_source_name(self) -> None:
        client = _ConcreteClient()
        assert client.source_name == "test_client"

    def test_default_health_check(self) -> None:
        """默认 health_check 返回健康。"""
        status = _ConcreteClient().health_check()
        assert status.healthy is True
        assert "未实现" in status.message

    def test_health_check_exception_handling(self) -> None:
        """health_check 异常应由调用方处理，客户端不吞异常。"""
        import pytest

        with pytest.raises(ConnectionError, match="模拟的网络错误"):
            _BrokenClient().health_check()

    def test_log_request_does_not_raise(self) -> None:
        client = _ConcreteClient()
        client._log_request("test_endpoint", {"param": 1})

    def test_log_response_does_not_raise(self) -> None:
        client = _ConcreteClient()
        client._log_response("test_endpoint", 10, 1234.0)

    def test_log_error_does_not_raise(self) -> None:
        client = _ConcreteClient()
        client._log_error("test_endpoint", ValueError("oops"), 500.0)

    def test_logger_uses_source_name(self) -> None:
        client = _ConcreteClient()
        assert client._logger.name == "client.test_client"
