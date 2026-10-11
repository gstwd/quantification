"""测试摄取服务层的 Tushare 优先编排（估值/宏观/成分降级逻辑）。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from quant_etf_api.infra.clients.akshare_index import IndexValuation
from quant_etf_api.infra.clients.akshare_macro import MacroIndicator
from quant_etf_api.services.ingest_service import IngestService


def _service() -> IngestService:
    """构造测试用 IngestService（DB 为 MagicMock）。"""
    db = MagicMock()
    return IngestService(db)


def _valuation(code: str, pe: float = 10.0) -> list[IndexValuation]:
    """构造单日估值样本。"""
    return [
        IndexValuation(
            trade_date=date(2026, 9, 8),
            pe=pe,
            pe_percentile=50.0,
            pb=1.0,
            pb_percentile=50.0,
            dividend_yield=None,
            source=code,
        )
    ]


class _FakeTushareValuation:
    """可配置的假 Tushare 估值客户端。"""

    def __init__(
        self,
        values: list[IndexValuation] | None = None,
        error: Exception | None = None,
        supported: bool = True,
    ) -> None:
        self.values = values
        self.error = error
        self.supported = supported

    def is_configured(self) -> bool:
        """模拟已配置 Token。"""
        return True

    def supports(self, index_code: str) -> bool:
        """按构造参数返回覆盖范围。"""
        return self.supported

    def fetch_index_valuation(self, index_code: str) -> list[IndexValuation]:
        """按预设返回数据或抛错。"""
        if self.error is not None:
            raise self.error
        return self.values or []


class _FakeAkShareValuation:
    """假 AkShare 估值客户端。"""

    def __init__(
        self, values: list[IndexValuation] | None = None, error: Exception | None = None
    ) -> None:
        self.values = values
        self.error = error

    def fetch_index_valuation(self, index_code: str) -> list[IndexValuation]:
        """返回预设数据或抛错。"""
        if self.error is not None:
            raise self.error
        return self.values or []


class TestValuationPreferred:
    """指数估值以乐咕乐股（AkShare）为主、Tushare 兜底的编排。"""

    def _patch(self, monkeypatch, *, tushare: _FakeTushareValuation, akshare: _FakeAkShareValuation) -> None:
        """替换两个估值客户端。"""
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.TushareIndexValuationClient", lambda: tushare
        )
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.AkShareIndexClient", lambda: akshare
        )

    def test_akshare_preferred_even_when_tushare_available(self, monkeypatch) -> None:
        """AkShare 成功时采用乐咕乐股，即使 Tushare 覆盖该指数（避免按天混用口径）。"""
        self._patch(
            monkeypatch,
            tushare=_FakeTushareValuation(values=_valuation("tushare")),
            akshare=_FakeAkShareValuation(values=_valuation("legulegu")),
        )
        result = _service()._fetch_index_valuation_preferred("000300")
        assert result[0].source == "legulegu"

    def test_akshare_empty_falls_back_to_tushare(self, monkeypatch) -> None:
        """AkShare 返回空时由 Tushare 兜底，并记录降级。"""
        self._patch(
            monkeypatch,
            tushare=_FakeTushareValuation(values=_valuation("tushare")),
            akshare=_FakeAkShareValuation(values=[]),
        )
        svc = _service()
        result = svc._fetch_index_valuation_preferred("000300")
        assert result[0].source == "tushare"
        assert svc._last_fallbacks[0]["from"] == "akshare"
        assert svc._last_fallbacks[0]["to"] == "tushare"

    def test_akshare_error_falls_back_to_tushare(self, monkeypatch) -> None:
        """AkShare 抛错时由 Tushare 兜底。"""
        self._patch(
            monkeypatch,
            tushare=_FakeTushareValuation(values=_valuation("tushare")),
            akshare=_FakeAkShareValuation(error=RuntimeError("legulegu down")),
        )
        result = _service()._fetch_index_valuation_preferred("000300")
        assert result[0].source == "tushare"

    def test_akshare_error_without_fallback_raises(self, monkeypatch) -> None:
        """Tushare 不覆盖该指数时，AkShare 的错误原样抛出，由调用方记为分区失败。"""
        import pytest

        self._patch(
            monkeypatch,
            tushare=_FakeTushareValuation(values=_valuation("tushare"), supported=False),
            akshare=_FakeAkShareValuation(error=RuntimeError("legulegu down")),
        )
        with pytest.raises(RuntimeError, match="legulegu down"):
            _service()._fetch_index_valuation_preferred("000852")

    def test_primary_source_replaces_tushare_fallback_rows(self, monkeypatch) -> None:
        """主来源恢复后写入时只覆盖 Tushare 兜底写入的行。"""
        svc = _service()
        monkeypatch.setattr(
            svc, "_fetch_index_valuation_preferred", lambda _code: _valuation("legulegu")
        )
        insert = MagicMock(return_value=1)
        monkeypatch.setattr(svc, "_insert_index_valuations", insert)

        assert svc._fetch_and_upsert_index_valuation("000300") == 1
        assert insert.call_args.kwargs == {"replace_sources": {"tushare"}}

    def test_fallback_rows_do_not_replace_existing(self, monkeypatch) -> None:
        """Tushare 兜底数据只补新日期，不覆盖已有主来源行。"""
        svc = _service()
        monkeypatch.setattr(
            svc, "_fetch_index_valuation_preferred", lambda _code: _valuation("tushare")
        )
        insert = MagicMock(return_value=1)
        monkeypatch.setattr(svc, "_insert_index_valuations", insert)

        svc._fetch_and_upsert_index_valuation("000300")
        assert insert.call_args.kwargs == {}

    def test_replace_sources_compiles_to_conditional_upsert(self) -> None:
        """replace_sources 生成带 WHERE 的 ON CONFLICT DO UPDATE，并按 rowcount 计数。"""
        from sqlalchemy.dialects import postgresql

        db = MagicMock()
        db.execute.return_value = MagicMock(rowcount=1)
        svc = IngestService(db)

        assert (
            svc._insert_index_valuations(
                "000300", _valuation("legulegu"), replace_sources={"tushare"}
            )
            == 1
        )
        sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT ON CONSTRAINT uq_index_valuation DO UPDATE" in sql
        assert "WHERE index_valuation.source IN" in sql


class _FakeMacroClient:
    """按组返回可配置的假宏观客户端。"""

    def __init__(
        self,
        cpi: list[MacroIndicator] | None = None,
        pmi: list[MacroIndicator] | None = None,
        lpr: list[MacroIndicator] | None = None,
        cpi_error: Exception | None = None,
        pmi_error: Exception | None = None,
        lpr_error: Exception | None = None,
        configured: bool = True,
    ) -> None:
        self._data = {"CPI": cpi, "PMI": pmi, "LPR": lpr}
        self._errors = {"CPI": cpi_error, "PMI": pmi_error, "LPR": lpr_error}
        self.configured = configured

    def is_configured(self) -> bool:
        """返回是否配置。"""
        return self.configured

    def _fetch(self, key: str) -> list[MacroIndicator]:
        """按组返回或抛错。"""
        if self._errors.get(key) is not None:
            raise self._errors[key]
        return self._data.get(key) or []

    def fetch_cpi_monthly(self) -> list[MacroIndicator]:
        """返回 CPI 组。"""
        return self._fetch("CPI")

    def fetch_pmi(self) -> list[MacroIndicator]:
        """返回 PMI 组。"""
        return self._fetch("PMI")

    def fetch_lpr(self) -> list[MacroIndicator]:
        """返回 LPR 组。"""
        return self._fetch("LPR")


def _macro(code: str, source: str) -> MacroIndicator:
    """构造宏观指标样本。"""
    return MacroIndicator(
        indicator_code=code,
        indicator_name=code,
        period="2026-08",
        value=1.0,
        unit="%",
        period_date="2026-08-01",
        source=source,
    )


class TestMacroPreferred:
    """宏观指标按组 Tushare 优先编排。"""

    def test_group_independent_fallback(self, monkeypatch) -> None:
        """LPR 组失败不影响 CPI/PMI 组。"""
        ts_client = _FakeMacroClient(
            cpi=[_macro("cpi", "tushare")],
            pmi=[_macro("pmi", "tushare")],
            lpr_error=RuntimeError("频次超限"),
        )
        ak_client = _FakeMacroClient(
            cpi=[_macro("cpi", "akshare")],
            pmi=[_macro("pmi", "akshare")],
            lpr=[_macro("lpr1y", "akshare")],
            configured=False,
        )
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.TushareMacroClient",
            lambda: ts_client,
        )
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.AkShareMacroClient",
            lambda: ak_client,
        )
        rows = _service()._fetch_macro_indicators_preferred()
        sources = {(row.indicator_code, row.source) for row in rows}
        assert ("cpi", "tushare") in sources
        assert ("pmi", "tushare") in sources
        assert ("lpr1y", "akshare") in sources

    def test_tushare_empty_falls_back(self, monkeypatch) -> None:
        """Tushare 某组返回空时该组回退 AkShare。"""
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.TushareMacroClient",
            lambda: _FakeMacroClient(pmi=[]),
        )
        ak_client = _FakeMacroClient(
            pmi=[_macro("pmi", "akshare")],
            configured=False,
        )
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.AkShareMacroClient",
            lambda: ak_client,
        )
        rows = _service()._fetch_macro_indicators_preferred()
        assert any(row.indicator_code == "pmi" and row.source == "akshare" for row in rows)
