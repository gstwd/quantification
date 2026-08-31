"""数据质量检测模块单元测试。"""

from datetime import date

from quant_etf_api.services.data_quality import check_daily_bar_anomalies


class _FakeBar:
    """最小日线桩对象，仅暴露检测所需属性。"""

    def __init__(self, **kwargs) -> None:
        self.trade_date = kwargs.get("trade_date", date(2026, 1, 5))
        self.code = kwargs.get("code", "000300")
        self.change_pct = kwargs.get("change_pct", 0.5)
        self.volume = kwargs.get("volume", 1000.0)
        self.close_price = kwargs.get("close_price", 100.0)
        self.open_price = kwargs.get("open_price", 100.0)
        self.high_price = kwargs.get("high_price", 101.0)
        self.low_price = kwargs.get("low_price", 99.0)


def test_complete_ohlc_no_anomaly() -> None:
    """OHLC 完整时不出异常记录。"""
    anomalies = check_daily_bar_anomalies([_FakeBar()])
    assert all(a.field != "open_price" for a in anomalies)


def test_null_ohlc_detected() -> None:
    """开盘/最高/最低为 None 时分别产出 error 级异常。"""
    bar = _FakeBar(open_price=None, high_price=None, low_price=None)
    anomalies = check_daily_bar_anomalies([bar])
    fields = {a.field for a in anomalies}
    assert fields == {"open_price", "high_price", "low_price"}
    assert all(a.severity == "error" for a in anomalies)
    assert all(a.code == "000300" for a in anomalies)


def test_non_positive_ohlc_detected() -> None:
    """开盘价 0 视为缺失并告警。"""
    anomalies = check_daily_bar_anomalies([_FakeBar(open_price=0.0)])
    assert any(a.field == "open_price" for a in anomalies)
