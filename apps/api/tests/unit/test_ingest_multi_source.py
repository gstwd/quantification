"""测试 IngestService 多数据源切换编排（优先级、严格 OHLC 校验、最少缺失兜底）。"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quant_etf_api.infra.clients.index_daily_common import IndexDailyBar
from quant_etf_api.services.ingest_service import IngestService


def _complete_bars(n: int = 3, base: float = 100.0) -> list[IndexDailyBar]:
    """构造 n 根 OHLC 完整的日线。"""
    start = date(2026, 1, 5)
    return [
        IndexDailyBar(
            trade_date=start + timedelta(days=i),
            open_price=base + i,
            close_price=base + i,
            high_price=base + i + 1,
            low_price=base + i - 1,
            volume=1000.0,
            turnover=1000000.0,
        )
        for i in range(n)
    ]


def _incomplete_bars(missing: int = 1) -> list[IndexDailyBar]:
    """构造前 missing 根开盘价缺失（NaN）的日线。"""
    bars = _complete_bars()
    for i in range(missing):
        bars[i] = IndexDailyBar(
            trade_date=bars[i].trade_date,
            open_price=float("nan"),
            close_price=bars[i].close_price,
            high_price=bars[i].high_price,
            low_price=bars[i].low_price,
            volume=1000.0,
            turnover=1000000.0,
        )
    return bars


class _FakeClient:
    """可配置返回值的假客户端。"""

    def __init__(
        self, bars: list[IndexDailyBar] | None = None, error: Exception | None = None
    ) -> None:
        self.bars = bars
        self.error = error

    def fetch_index_daily(self, *args, **kwargs) -> list[IndexDailyBar]:
        """按预设返回数据或抛出异常。"""
        if self.error is not None:
            raise self.error
        return self.bars or []


def _make_service() -> IngestService:
    """构造测试用 IngestService（DB 为 MagicMock）。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    return IngestService(db)


def _patch_settings(monkeypatch, source_order: str = "efinance,akshare,tushare,pytdx,baostock"):
    """替换 get_settings 返回假配置。"""
    monkeypatch.setattr(
        "quant_etf_api.services.ingest_service.get_settings",
        lambda: SimpleNamespace(
            index_daily_source_order=source_order,
            tushare_token="",
        ),
    )


class TestBuildIndexDailySources:
    """数据源列表构建。"""

    def test_order_and_tushare_skip(self, monkeypatch) -> None:
        """按配置顺序构建；未配置 Token 时跳过 tushare。"""
        _patch_settings(monkeypatch)
        svc = _make_service()
        sources = svc._build_index_daily_sources()
        assert [name for name, _ in sources] == ["efinance", "akshare", "pytdx", "baostock"]

    def test_unknown_source_ignored(self, monkeypatch) -> None:
        """未知数据源名被忽略并告警。"""
        _patch_settings(monkeypatch, source_order="efinance,unknown,baostock")
        svc = _make_service()
        sources = svc._build_index_daily_sources()
        assert [name for name, _ in sources] == ["efinance", "baostock"]

    def test_custom_order(self, monkeypatch) -> None:
        """支持自定义优先级（如 baostock 优先）。"""
        _patch_settings(monkeypatch, source_order="baostock,efinance")
        svc = _make_service()
        sources = svc._build_index_daily_sources()
        assert [name for name, _ in sources] == ["baostock", "efinance"]


class TestFetchIndexDailyMultiSource:
    """多源切换核心逻辑。"""

    def test_first_complete_source_wins(self, monkeypatch) -> None:
        """首个 OHLC 完整的数据源直接采用（按优先级）。"""
        svc = _make_service()
        sources = [
            ("efinance", _FakeClient(_complete_bars())),
            ("akshare", _FakeClient(_complete_bars())),
        ]
        monkeypatch.setattr(svc, "_build_index_daily_sources", lambda: sources)
        bars, source = svc._fetch_index_daily_multi_source("000300")
        assert source == "efinance"
        assert len(bars) == 3

    def test_incomplete_source_falls_through(self, monkeypatch) -> None:
        """任一缺失即不合格，继续降级到完整源。"""
        svc = _make_service()
        sources = [
            ("efinance", _FakeClient(_incomplete_bars(1))),
            ("akshare", _FakeClient(_complete_bars())),
        ]
        monkeypatch.setattr(svc, "_build_index_daily_sources", lambda: sources)
        bars, source = svc._fetch_index_daily_multi_source("000300")
        assert source == "akshare"
        assert len(bars) == 3

    def test_least_missing_fallback(self, monkeypatch) -> None:
        """所有源均不完整时采用缺失交易日最少的数据源。"""
        svc = _make_service()
        sources = [
            ("efinance", _FakeClient(_incomplete_bars(2))),
            ("akshare", _FakeClient(_incomplete_bars(1))),
        ]
        monkeypatch.setattr(svc, "_build_index_daily_sources", lambda: sources)
        bars, source = svc._fetch_index_daily_multi_source("000300")
        assert source == "akshare"  # 缺失 1 日 < 缺失 2 日
        assert len(bars) == 3

    def test_all_fail_raises_last_error(self, monkeypatch) -> None:
        """全部源抛错时抛出最后一个异常。"""
        svc = _make_service()
        sources = [
            ("efinance", _FakeClient(error=RuntimeError("efinance down"))),
            ("akshare", _FakeClient(error=RuntimeError("akshare down"))),
        ]
        monkeypatch.setattr(svc, "_build_index_daily_sources", lambda: sources)
        with pytest.raises(RuntimeError, match="akshare down"):
            svc._fetch_index_daily_multi_source("000300")

    def test_all_empty_returns_empty(self, monkeypatch) -> None:
        """全部返回空数据时返回空列表。"""
        svc = _make_service()
        sources = [
            ("efinance", _FakeClient([])),
            ("akshare", _FakeClient([])),
        ]
        monkeypatch.setattr(svc, "_build_index_daily_sources", lambda: sources)
        bars, source = svc._fetch_index_daily_multi_source("000300")
        assert bars == []
        assert source == ""

    def test_retryable_failure_then_success(self, monkeypatch) -> None:
        """某源失败后继续降级，最终由后续完整源提供。"""
        svc = _make_service()
        sources = [
            ("efinance", _FakeClient(error=ConnectionError("timeout"))),
            ("baostock", _FakeClient(_complete_bars())),
        ]
        monkeypatch.setattr(svc, "_build_index_daily_sources", lambda: sources)
        bars, source = svc._fetch_index_daily_multi_source("000300")
        assert source == "baostock"
        assert len(bars) == 3


class TestFetchAndUpsertIndexBars:
    """入库路径：记录实际数据源 + 增量过滤。"""

    def test_source_recorded_and_commit(self, monkeypatch) -> None:
        """全量拉取时把实际数据源写入 _insert_index_bars 并提交。"""
        svc = _make_service()
        monkeypatch.setattr(
            svc,
            "_fetch_index_daily_multi_source",
            lambda *a, **k: (_complete_bars(), "baostock"),
        )
        inserted: dict = {}

        def fake_insert(index_code: str, bars: list, source: str = "akshare") -> int:
            inserted["index_code"] = index_code
            inserted["source"] = source
            inserted["count"] = len(bars)
            return len(bars)

        monkeypatch.setattr(svc, "_insert_index_bars", fake_insert)
        svc._index_bar_repo.get_latest_date = lambda code: None  # 全量模式
        count = svc._fetch_and_upsert_index_bars("000300", incremental=False)

        assert count == 3
        assert inserted["source"] == "baostock"
        assert inserted["index_code"] == "000300"
        svc._db.commit.assert_called_once()

    def test_incremental_filters_after_latest(self, monkeypatch) -> None:
        """增量模式仅保留 DB 最新日期之后的数据（丢弃缓冲窗口重复行）。"""
        svc = _make_service()
        latest = date(2026, 1, 6)
        svc._index_bar_repo.get_latest_date = lambda code: latest
        bars = _complete_bars(5, base=100.0)
        monkeypatch.setattr(
            svc,
            "_fetch_index_daily_multi_source",
            lambda *a, **k: (bars, "efinance"),
        )
        captured: dict = {}

        def fake_insert(index_code: str, rows: list, source: str = "akshare") -> int:
            captured["rows"] = rows
            captured["source"] = source
            return len(rows)

        monkeypatch.setattr(svc, "_insert_index_bars", fake_insert)
        svc._fetch_and_upsert_index_bars("000300", incremental=True)

        # _complete_bars(5) 日期为 01-05~01-09，仅保留 > 01-06 的 3 条
        assert len(captured["rows"]) == 3
        assert all(r.trade_date > latest for r in captured["rows"])
        assert captured["source"] == "efinance"


class TestRebuildIndexDataMultiSource:
    """单指数全量覆盖重拉应走多数据源并记录实际数据源。"""

    def test_rebuild_uses_multi_source(self, monkeypatch) -> None:
        """rebuild 通过 _fetch_index_daily_multi_source 拉取，而非直接 AkShare。"""
        svc = _make_service()
        # 指数存在，可获取摄取锁
        svc._index_repo.find_by_code = lambda code: object()
        # 运行生命周期用假服务，便于断言状态流转
        fake_run_svc = MagicMock()
        svc._run_svc = fake_run_svc
        monkeypatch.setattr(
            svc,
            "_fetch_index_daily_multi_source",
            lambda *a, **k: (_complete_bars(), "pytdx"),
        )
        # 估值仍走 AkShare 客户端（多源仅覆盖日线）
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.AkShareIndexClient",
            lambda: MagicMock(fetch_index_valuation=MagicMock(return_value=[])),
        )
        inserted: dict = {}

        def fake_insert(index_code: str, bars: list, source: str = "akshare") -> int:
            inserted["index_code"] = index_code
            inserted["source"] = source
            inserted["count"] = len(bars)
            return len(bars)

        monkeypatch.setattr(svc, "_insert_index_bars", fake_insert)
        # 删除旧数据的 DB 链
        svc._db.query.return_value.filter.return_value.delete.return_value = 5

        svc.rebuild_index_data("run-1", "000300")

        assert inserted["source"] == "pytdx"
        assert inserted["index_code"] == "000300"
        assert inserted["count"] == 3
        svc._db.commit.assert_called()
        fake_run_svc.mark_success.assert_called_once()
        # 运行指标应记录实际数据源，便于排查走了哪个源
        metrics = fake_run_svc.mark_success.call_args.kwargs["metrics"]
        assert metrics["bar_source"] == "pytdx"
