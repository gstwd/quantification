"""数据质量检查兼容模块。

检查规则已下沉到 :mod:`quant_etf_api.domain.market_data.quality`；本模块只
保留历史导入路径的转发，不再承载数据质量业务逻辑。
"""

from quant_etf_api.domain.market_data.quality import (
    Anomaly,
    check_continuity,
    check_daily_bar_anomalies,
    check_valuation_anomalies,
)

__all__ = [
    "Anomaly",
    "check_continuity",
    "check_daily_bar_anomalies",
    "check_valuation_anomalies",
]
