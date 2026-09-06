"""个股收盘客户端健壮性测试（baostock 登录返回 None 的判空）。"""

from __future__ import annotations

import sys

import pytest

from quant_etf_api.infra.clients.stock_close_client import StockCloseClient


def test_baostock_login_none_raises_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """baostock login 返回 None 时应报可读错误而非 NoneType 异常。"""

    class FakeBaostock:
        """仅提供 login/logout 的 baostock 替身。"""

        @staticmethod
        def login():
            return None

        @staticmethod
        def logout():
            return None

    monkeypatch.setitem(sys.modules, "baostock", FakeBaostock())
    client = StockCloseClient()
    with pytest.raises(RuntimeError, match="登录返回空对象"):
        client.fetch_history_baostock("600000", "20130101", "20200101")


def test_baostock_login_failure_raises_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """baostock 登录 error_code 非 0 时抛出含错误信息的 RuntimeError。"""

    class FakeLoginResult:
        """baostock 登录失败结果。"""

        error_code = "10001"
        error_msg = "login failed"

    class FakeBaostock:
        """返回失败登录结果的 baostock 替身。"""

        @staticmethod
        def login():
            return FakeLoginResult()

        @staticmethod
        def logout():
            return None

    monkeypatch.setitem(sys.modules, "baostock", FakeBaostock())
    client = StockCloseClient()
    with pytest.raises(RuntimeError, match="baostock 登录失败: login failed"):
        client.fetch_history_baostock("600000", "20130101", "20200101")
