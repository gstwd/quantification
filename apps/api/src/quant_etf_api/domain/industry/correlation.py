"""行业与指数收益相关度纯计算规则。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_industry_index_correlations(
    index_close: pd.Series,
    industry_close: pd.DataFrame,
) -> pd.DataFrame:
    """计算行业与单个指数的 Pearson 日收益相关度。

    输入以收盘价为准，先在完整日期轴上计算简单日收益。任一资产在某日缺少
    收盘价时，该日及其下一日（缺少前值）收益均为空，因而不会用前填或跨缺口
    收益替代。每个行业只在与指数同时具有有效日收益的样本上计算相关度。

    Args:
        index_close: 指数收盘价序列，索引为交易日。
        industry_close: 行业收盘价宽表，列名为行业代码，索引为交易日。

    Returns:
        以行业代码为索引、包含 correlation 与 sample_count 列的数据框；
        样本少于两条或任一序列方差为零时 correlation 为 NaN。
    """
    if industry_close.empty:
        return pd.DataFrame(columns=["correlation", "sample_count"])

    combined = pd.concat([index_close.rename("__index__"), industry_close], axis=1).sort_index()
    returns = combined.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    rows: list[dict[str, float | int | str]] = []
    for industry_code in industry_close.columns:
        pair = returns[["__index__", industry_code]].dropna()
        sample_count = len(pair)
        correlation = np.nan
        if sample_count >= 2:
            correlation = pair["__index__"].corr(pair[industry_code])
        rows.append(
            {
                "industry_code": str(industry_code),
                "correlation": correlation,
                "sample_count": sample_count,
            }
        )
    return pd.DataFrame(rows).set_index("industry_code")
