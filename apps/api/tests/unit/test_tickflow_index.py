"""测试 TickFlowIndexClient（TickFlow 指数日线，默认免费服务）。"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from quant_etf_api.infra.clients.index_daily_common import is_ohlc_complete
from quant_etf_api.infra.clients.tickflow_index import TickFlowIndexClient

_CN_TZ = ZoneInfo("Asia/Shanghai")


def _ms(year: int, month: int, day: int, *, end_of_day: bool = False) -> int:
    """构造测试用毫秒时间戳（Asia/Shanghai）。"""
    dt = datetime(year, month, day, tzinfo=_CN_TZ)
    if end_of_day:
        dt = dt + timedelta(days=1)
        return int(dt.timestamp() * 1000) - 1
    return int(dt.timestamp() * 1000)


def _rows(pairs: list[tuple[str, float, float, float, float, float, float]]) -> dict:
    """将 (日期, OHLC, 量, 额) 元组转为 TickFlow 紧凑列式响应。"""
    timestamps = [
        int(
            datetime.combine(date.fromisoformat(pair[0]), time.min, tzinfo=_CN_TZ).timestamp()
            * 1000
        )
        for pair in pairs
    ]
    return {
        "timestamp": timestamps,
        "open": [p[1] for p in pairs],
        "high": [p[2] for p in pairs],
        "low": [p[3] for p in pairs],
        "close": [p[4] for p in pairs],
        "volume": [p[5] for p in pairs],
        "amount": [p[6] for p in pairs],
    }


class _FakeKlines:
    """记录参数并按预设响应返回的假 klines 接口。"""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, symbol: str, **kwargs) -> dict:
        """记录调用并弹出下一条响应。"""
        self.calls.append({"symbol": symbol, **kwargs})
        return self.responses.pop(0) if self.responses else {"timestamp": []}


class _FakeTickFlow:
    """假 TickFlow 客户端，用于替换 tickflow 模块。"""

    instances: list["_FakeTickFlow"] = []

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        **_kwargs,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.klines = _FakeKlines(_FakeTickFlow._responses.pop(0))
        _FakeTickFlow.instances.append(self)

    @classmethod
    def reset(cls, responses: list[dict]) -> None:
        """重置实例列表并设置后续响应队列。"""
        cls.instances = []
        cls._responses = [responses]

    def close(self) -> None:
        """假客户端无需释放资源。"""


def _patch_tickflow(responses: list[dict]):
    """注入假 tickflow 模块并返回客户端。"""
    _FakeTickFlow.reset(responses)
    fake_module = SimpleNamespace(TickFlow=_FakeTickFlow)
    return patch.dict(sys.modules, {"tickflow": fake_module})


def _sample_rows() -> list[dict]:
    """构造 3 根标准指数日 K（volume=手、amount=元）。"""
    return [
        _rows(
            [
                ("2026-01-05", 4661.62, 4721.64, 4661.62, 4717.75, 234144839.0, 630577351311.0),
                ("2026-01-06", 4719.21, 4790.69, 4718.01, 4790.69, 296970686.0, 725414566873.0),
                ("2026-01-07", 4796.93, 4802.60, 4751.07, 4776.67, 259095564.0, 664897929361.0),
            ]
        )
    ]


class TestTickFlowIndexClient:
    """TickFlow 指数日线客户端。"""

    def test_symbol_mapping(self) -> None:
        """指数代码按 sh/sz 归属映射为 code.SH/code.SZ。"""
        assert TickFlowIndexClient._symbol("000300") == "000300.SH"
        assert TickFlowIndexClient._symbol("399001") == "399001.SZ"
        assert TickFlowIndexClient._symbol("H30269") == "H30269.SH"

    def test_fetch_free_mode_params_and_units(self) -> None:
        """默认免费服务：无 Key、请求参数正确、量额单位原样入库。"""
        with _patch_tickflow(_sample_rows()):
            client = TickFlowIndexClient()
            bars = client.fetch_index_daily("000300", "20260101", "20260131")

        instance = _FakeTickFlow.instances[0]
        assert instance.api_key is None
        assert instance.base_url == "https://free-api.tickflow.org"
        call = instance.klines.calls[0]
        assert call["symbol"] == "000300.SH"
        assert call["period"] == "1d"
        assert call["adjust"] == "none"
        assert call["count"] == 10000
        assert call["start_time"] == _ms(2026, 1, 1)
        assert call["end_time"] == _ms(2026, 1, 31, end_of_day=True)
        assert len(bars) == 3
        assert bars[0].volume == 234144839.0  # 手，无需换算
        assert bars[0].turnover == 630577351311.0  # 元，无需换算
        assert is_ohlc_complete(bars) is True

    def test_paid_mode_when_key_configured(self) -> None:
        """配置 API Key 时切换到正式服务地址。"""
        with _patch_tickflow(_sample_rows()):
            client = TickFlowIndexClient(api_key="test-key")
            client.fetch_index_daily("000300")

        instance = _FakeTickFlow.instances[0]
        assert instance.api_key == "test-key"
        assert instance.base_url == "https://api.tickflow.org"

    def test_fetch_sz_suffix(self) -> None:
        """深证指数使用 .SZ 后缀。"""
        with _patch_tickflow(_sample_rows()):
            client = TickFlowIndexClient()
            client.fetch_index_daily("399001")
        assert _FakeTickFlow.instances[0].klines.calls[0]["symbol"] == "399001.SZ"

    def test_local_date_filter(self) -> None:
        """超出请求窗口的本地行被过滤（服务端未过滤的兜底）。"""
        rows = _rows(
            [
                ("2026-01-05", 1.0, 2.0, 0.5, 1.5, 10.0, 100.0),
                ("2026-02-01", 3.0, 4.0, 2.5, 3.5, 20.0, 200.0),
            ]
        )
        with _patch_tickflow([rows]):
            client = TickFlowIndexClient()
            bars = client.fetch_index_daily("000300", "20260101", "20260131")
        assert [b.trade_date.isoformat() for b in bars] == ["2026-01-05"]

    def test_missing_amount_filled_zero(self) -> None:
        """响应缺少 amount 字段时按 0 补齐，不影响 OHLC。"""
        rows = _rows(
            [
                ("2026-01-05", 1.0, 2.0, 0.5, 1.5, 10.0, 100.0),
            ]
        )
        rows.pop("amount")
        with _patch_tickflow([rows]):
            client = TickFlowIndexClient()
            bars = client.fetch_index_daily("000300")
        assert len(bars) == 1
        assert bars[0].turnover == 0.0
        assert is_ohlc_complete(bars) is True

    def test_empty_response_returns_empty(self) -> None:
        """不支持/无数据标的返回空列表（交由多源链降级）。"""
        with _patch_tickflow([{"timestamp": []}]):
            client = TickFlowIndexClient()
            assert client.fetch_index_daily("H30269") == []

    def test_pagination_merges_ascending(self, monkeypatch) -> None:
        """历史超过单次上限时向前翻页并合并去重。"""
        monkeypatch.setattr(
            "quant_etf_api.infra.clients.tickflow_index._TICKFLOW_MAX_ROWS_PER_REQUEST",
            2,
        )
        monkeypatch.setattr(
            "quant_etf_api.infra.clients.tickflow_index._TICKFLOW_MIN_INTERVAL",
            0.0,
        )
        page1 = _rows(
            [
                ("2026-01-06", 1.0, 2.0, 0.5, 1.5, 10.0, 100.0),
                ("2026-01-07", 2.0, 3.0, 1.5, 2.5, 20.0, 200.0),
            ]
        )
        page2 = _rows(
            [
                ("2026-01-05", 0.5, 1.5, 0.2, 1.0, 5.0, 50.0),
            ]
        )
        with _patch_tickflow([page1, page2]):
            client = TickFlowIndexClient()
            bars = client.fetch_index_daily("000300", "20260101", "20260131")

        assert len(_FakeTickFlow.instances[0].klines.calls) == 2
        assert [b.trade_date.isoformat() for b in bars] == [
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
        ]

    def test_fetch_error_propagates(self) -> None:
        """上游异常向上抛出（由 IngestService 多源链降级）。"""

        class _BrokenKlines:
            """抛出异常的假 klines。"""

            def get(self, *args, **kwargs) -> dict:
                """按约定抛出异常。"""
                raise RuntimeError("tickflow 不可用")

        class _BrokenTickFlow(_FakeTickFlow):
            """带异常 klines 的假客户端。"""

            def __init__(self, *args, **kwargs) -> None:
                self.klines = _BrokenKlines()

        fake_module = SimpleNamespace(TickFlow=_BrokenTickFlow)
        with patch.dict(sys.modules, {"tickflow": fake_module}):
            client = TickFlowIndexClient()
            with pytest.raises(RuntimeError, match="tickflow 不可用"):
                client.fetch_index_daily("000300")

    def test_is_configured_without_sdk(self) -> None:
        """SDK 未安装时 is_configured 返回 False。"""
        with patch.dict(sys.modules, {"tickflow": None}):
            assert TickFlowIndexClient().is_configured() is False
