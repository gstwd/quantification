"""行业与指数严格收益相关度纯算法测试。"""

from __future__ import annotations

import pandas as pd

from quant_etf_api.domain.industry.correlation import compute_industry_index_correlations


def test_correlation_uses_only_continuous_aligned_returns() -> None:
    """缺失收盘不会前填，缺口后的跨日收益也不应进入相关度样本。"""
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    index_close = pd.Series([100.0, 110.0, 121.0, 133.1, 146.41], index=dates)
    industry_close = pd.DataFrame(
        {
            "801010": [50.0, 55.0, None, 66.55, 73.205],
            "801020": [30.0, 33.0, 36.3, 39.93, 43.923],
        },
        index=dates,
    )

    result = compute_industry_index_correlations(index_close, industry_close)

    # 801010 仅第 2 天与第 5 天可形成连续、共同的有效收益。
    assert result.loc["801010", "sample_count"] == 2
    assert result.loc["801010", "correlation"] == 1.0
    assert result.loc["801020", "sample_count"] == 4
    assert result.loc["801020", "correlation"] == 1.0
