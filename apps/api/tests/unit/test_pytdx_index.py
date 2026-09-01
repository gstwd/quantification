"""测试 PytdxIndexClient（通达信指数日线）。"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from quant_etf_api.infra.clients.index_daily_common import is_ohlc_complete
from quant_etf_api.infra.clients.pytdx_index import PytdxIndexClient


def _kline_row_for(d: date) -> dict:
    """构造指定日期的一根 pytdx 指数日 K 线。"""
    ordinal = d.toordinal()
    return {
        "datetime": f"{d.isoformat()} 15:00",
        "open": float(ordinal),
        "close": float(ordinal) + 10.0,
        "high": float(ordinal) + 20.0,
        "low": float(ordinal) - 10.0,
        "vol": 234144839.0,
        "amount": 6.30577351311e11,
    }


def _kline_chunk(newest: date, n: int) -> list[dict]:
    """构造 n 根 K 线分块（pytdx 按最新在前返回）。"""
    return [_kline_row_for(newest - timedelta(days=i)) for i in range(n)]


class _FakeTdxApi:
    """模拟 pytdx TdxHq_API：记录参数并按需返回 K 线分块。"""

    def __init__(self, chunks: list[list[dict]]) -> None:
        self.chunks = chunks
        self.calls: list[tuple] = []
        self.connected = False

    def connect(self, host: str, port: int, time_out: int = 5) -> bool:
        """模拟连接成功。"""
        self.connected = True
        return True

    def disconnect(self) -> None:
        """模拟断开连接。"""
        self.connected = False

    def get_index_bars(
        self, category: int, market: int, code: str, start: int, count: int
    ) -> list[dict]:
        """按 start 偏移返回对应分块。"""
        self.calls.append((category, market, code, start, count))
        idx = start // 800 if self.chunks else 0
        if idx >= len(self.chunks):
            return []
        return self.chunks[idx]


class TestPytdxIndexClient:
    """通达信指数日线客户端。"""

    def test_market_mapping_and_pagination(self) -> None:
        """沪市指数 market=1；请求起点已覆盖时不再继续分页。"""
        chunk1 = _kline_chunk(date(2026, 1, 7), 800)
        api = _FakeTdxApi([chunk1])

        with patch.object(PytdxIndexClient, "_get_tdx_api", return_value=lambda: api):
            client = PytdxIndexClient(hosts=[("127.0.0.1", 7709)])
            # 请求 2026-01-07 起的数据：第一块已覆盖，无需第二块
            bars = client.fetch_index_daily("000300", "20260107", "20260131")

        assert api.calls[0] == (9, 1, "000300", 0, 800)
        assert len(api.calls) == 1  # 第一块已覆盖起点，未继续分页
        assert len(bars) == 1
        assert bars[0].trade_date == date(2026, 1, 7)
        assert is_ohlc_complete(bars) is True

    def test_sz_market(self) -> None:
        """深证指数使用 market=0。"""
        api = _FakeTdxApi([[_kline_row_for(date(2026, 1, 7))]])
        with patch.object(PytdxIndexClient, "_get_tdx_api", return_value=lambda: api):
            client = PytdxIndexClient(hosts=[("127.0.0.1", 7709)])
            client.fetch_index_daily("399001")
        assert api.calls[0][1] == 0

    def test_pagination_until_start_covered(self) -> None:
        """起点未被第一块覆盖时按 800 偏移继续分页。"""
        start = date(2026, 1, 5)
        # 第一块 800 根：最新 start+800 天，最旧 start+1 天（未覆盖 start）
        chunk1 = _kline_chunk(start + timedelta(days=800), 800)
        # 第二块 1 根：恰好覆盖 start
        chunk2 = [_kline_row_for(start)]
        chunks = [chunk1, chunk2]
        api = _FakeTdxApi(chunks)
        with patch.object(PytdxIndexClient, "_get_tdx_api", return_value=lambda: api):
            client = PytdxIndexClient(hosts=[("127.0.0.1", 7709)])
            # 起点 2026-01-05：第一块最旧为 01-06，未覆盖，继续第二块
            bars = client.fetch_index_daily("000300", "20260105", "20281231")
        assert len(api.calls) == 2
        assert api.calls[1][3] == 800
        assert len(bars) == 801
        # pytdx 最新在前，转换后必须按日期升序
        assert [b.trade_date for b in bars] == sorted(b.trade_date for b in bars)

    def test_fetch_since(self) -> None:
        """增量拉取使用缓冲窗口起点。"""
        api = _FakeTdxApi([[_kline_row_for(date(2026, 1, 7))]])
        with patch.object(PytdxIndexClient, "_get_tdx_api", return_value=lambda: api):
            client = PytdxIndexClient(hosts=[("127.0.0.1", 7709)])
            client.fetch_index_daily_since("000300", date(2026, 1, 20))
        assert api.calls[0][3] == 0

    def test_cooldown_after_all_connect_fail(self) -> None:
        """所有服务器连接失败后进入冷却，再次调用直接失败。"""

        class _FailingApi:
            def connect(self, host: str, port: int, time_out: int = 5) -> bool:
                return False

            def disconnect(self) -> None:
                return None

        with patch.object(PytdxIndexClient, "_get_tdx_api", return_value=_FailingApi):
            client = PytdxIndexClient(hosts=[("127.0.0.1", 7709)])
            try:
                client.fetch_index_daily("000300")
            except RuntimeError:
                pass
            assert client._is_in_connection_cooldown() is True
