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

    def __init__(self, values: list[IndexValuation] | None = None) -> None:
        self.values = values

    def fetch_index_valuation(self, index_code: str) -> list[IndexValuation]:
        """返回预设数据。"""
        return self.values or []


class TestValuationPreferred:
    """指数估值 Tushare 优先编排。"""

    def test_tushare_success_used(self, monkeypatch) -> None:
        """Tushare 覆盖且成功时直接采用 tushare 结果。"""
        ts_values = _valuation("tushare")
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.TushareIndexValuationClient",
            lambda: _FakeTushareValuation(values=ts_values),
        )
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.AkShareIndexClient",
            lambda: _FakeAkShareValuation(values=_valuation("legulegu")),
        )
        result = _service()._fetch_index_valuation_preferred("000300")
        assert result[0].source == "tushare"

    def test_tushare_empty_falls_back(self, monkeypatch) -> None:
        """Tushare 返回空时回退 AkShare。"""
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.TushareIndexValuationClient",
            lambda: _FakeTushareValuation(values=[]),
        )
        ak_values = _valuation("legulegu")
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.AkShareIndexClient",
            lambda: _FakeAkShareValuation(values=ak_values),
        )
        result = _service()._fetch_index_valuation_preferred("000300")
        assert result[0].source == "legulegu"

    def test_unsupported_skips_tushare(self, monkeypatch) -> None:
        """Tushare 未覆盖的指数直接走 AkShare。"""
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.TushareIndexValuationClient",
            lambda: _FakeTushareValuation(
                values=_valuation("tushare"), supported=False
            ),
        )
        monkeypatch.setattr(
            "quant_etf_api.services.ingest_service.AkShareIndexClient",
            lambda: _FakeAkShareValuation(values=_valuation("csindex")),
        )
        result = _service()._fetch_index_valuation_preferred("000852")
        assert result[0].source == "csindex"


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
