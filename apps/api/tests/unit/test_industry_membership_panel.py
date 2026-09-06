"""行业成分事件→日频归属矩阵构建测试。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from quant_etf_api.services.industry_factor_service import _membership_panel


def test_membership_panel_latest_event_wins() -> None:
    """每只股票取生效日不晚于当日的最新事件作为归属。"""
    events = [
        SimpleNamespace(stock_code="600000", industry_code="801780", start_date=date(2014, 2, 21)),
        SimpleNamespace(stock_code="600000", industry_code="801230", start_date=date(2023, 1, 1)),
        SimpleNamespace(stock_code="000001", industry_code="801010", start_date=date(2020, 6, 1)),
    ]
    dates = pd.DatetimeIndex(["2022-01-03", "2023-06-01", "2024-01-02"])
    stocks = pd.Index(["600000", "000001"])
    frame, industry_codes = _membership_panel(events, dates, stocks)
    # 2022 年 600000 属于 801780；2023/2024 年转属 801230
    assert frame.iloc[0]["600000"] == industry_codes.index("801780")
    assert frame.iloc[1]["600000"] == industry_codes.index("801230")
    assert frame.iloc[2]["000001"] == industry_codes.index("801010")
