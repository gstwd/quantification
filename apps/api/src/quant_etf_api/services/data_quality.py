"""数据质量检查模块。

提供日线、估值、连续性等维度的自动异常检测，
在数据摄取后调用，将异常记录到日志中。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Anomaly:
    """数据异常记录。

    Attributes:
        date: 异常发生的日期。
        code: 资产/指数代码。
        field: 异常字段名。
        value: 异常值。
        expected: 预期范围描述。
        severity: 严重程度：info/warning/error。
    """

    date: str
    code: str
    field: str
    value: Any
    expected: str
    severity: str = "warning"


def check_daily_bar_anomalies(
    bars: list[Any],
) -> list[Anomaly]:
    """检查日线数据中的异常值。

    检测项：
    - 涨跌幅超过 ±15%（指数）
    - 成交量为 0 但价格有变动
    - 收盘价 <= 0
    - 开盘/最高/最低缺失或非正（上游部分历史区间仅提供收盘价，影响回测调仓）

    Args:
        bars: 日线数据行列表（需有 .trade_date, .code, .change_pct, .volume, .close_price 属性）。

    Returns:
        异常记录列表。
    """
    anomalies: list[Anomaly] = []
    for b in bars:
        code = getattr(b, "code", getattr(b, "index_code", "?"))
        td = str(getattr(b, "trade_date", "?"))

        # 涨跌幅异常
        change = getattr(b, "change_pct", None)
        if change is not None:
            if abs(change) > 15.0:
                anomalies.append(
                    Anomaly(
                        date=td,
                        code=code,
                        field="change_pct",
                        value=round(change, 2),
                        expected="±15% 以内",
                        severity="error",
                    )
                )

        # 零量异动
        volume = getattr(b, "volume", None)
        if volume is not None and volume == 0 and change is not None and abs(change) > 0.01:
            anomalies.append(
                Anomaly(
                    date=td,
                    code=code,
                    field="volume",
                    value=0,
                    expected=">0（有价格变动）",
                    severity="warning",
                )
            )

        # 收盘价异常
        close = getattr(b, "close_price", None)
        if close is not None and close <= 0:
            anomalies.append(
                Anomaly(
                    date=td,
                    code=code,
                    field="close_price",
                    value=close,
                    expected=">0",
                    severity="error",
                )
            )

        # OHLC 完整性：有行情记录但开盘/最高/最低缺失或非正
        # （如中证官网对红利低波等指数 2016-2018 年部分交易日仅返回收盘价）
        for ohlc_field in ("open_price", "high_price", "low_price"):
            ohlc_val = getattr(b, ohlc_field, None)
            if (
                ohlc_val is None
                or (isinstance(ohlc_val, float) and math.isnan(ohlc_val))
                or ohlc_val <= 0
            ):
                anomalies.append(
                    Anomaly(
                        date=td,
                        code=code,
                        field=ohlc_field,
                        value=ohlc_val,
                        expected=">0（完整 OHLC）",
                        severity="error",
                    )
                )

    if anomalies:
        logger.warning("日线异常检测发现 %d 个问题", len(anomalies))
    return anomalies


def check_valuation_anomalies(
    valuations: list[Any],
) -> list[Anomaly]:
    """检查估值数据中的异常值。

    检测项：
    - PE/PB 为负数
    - PE/PB 百分位超出 [0, 100]

    Args:
        valuations: 估值数据行列表（需有 .trade_date, .index_code, .pe, .pb, .pe_percentile, .pb_percentile）。

    Returns:
        异常记录列表。
    """
    anomalies: list[Anomaly] = []
    for v in valuations:
        code = getattr(v, "index_code", "?")
        td = str(getattr(v, "trade_date", "?"))

        for field_name in ("pe", "pb"):
            val = getattr(v, field_name, None)
            if val is not None and val < 0:
                anomalies.append(
                    Anomaly(
                        date=td,
                        code=code,
                        field=field_name,
                        value=round(val, 2),
                        expected=">=0",
                        severity="warning",
                    )
                )

        for pct_field in ("pe_percentile", "pb_percentile"):
            pct_val = getattr(v, pct_field, None)
            if pct_val is not None and (pct_val < 0 or pct_val > 100):
                anomalies.append(
                    Anomaly(
                        date=td,
                        code=code,
                        field=pct_field,
                        value=round(pct_val, 2),
                        expected="[0, 100]",
                        severity="error",
                    )
                )

    if anomalies:
        logger.warning("估值异常检测发现 %d 个问题", len(anomalies))
    return anomalies


def check_continuity(
    bars: list[Any],
    trading_days: set[date] | None = None,
) -> list[Anomaly]:
    """检查日线数据的连续性，检测是否存在交易日期缺口。

    Args:
        bars: 日线数据行列表（按 code 分组，每个 code 内按 trade_date 排序）。
        trading_days: 已知交易日集合，用于判断缺口是否为正常休市。

    Returns:
        异常记录列表。
    """
    if not bars:
        return []

    # 按 code 分组
    by_code: dict[str, list[Any]] = {}
    for b in bars:
        code = getattr(b, "code", getattr(b, "index_code", "?"))
        by_code.setdefault(code, []).append(b)

    anomalies: list[Anomaly] = []
    for code, code_bars in by_code.items():
        code_bars.sort(key=lambda x: getattr(x, "trade_date", date.min))
        for i in range(1, len(code_bars)):
            prev_date = getattr(code_bars[i - 1], "trade_date", None)
            curr_date = getattr(code_bars[i], "trade_date", None)
            if prev_date is None or curr_date is None:
                continue
            gap = (curr_date - prev_date).days
            if gap > 5:  # 超过 5 天视为异常缺口
                if trading_days is None:
                    anomalies.append(
                        Anomaly(
                            date=str(curr_date),
                            code=code,
                            field="trade_date",
                            value=f"距上次 {gap} 天",
                            expected="≤5 天间隔",
                            severity="warning",
                        )
                    )
                else:
                    # 检查中间是否有交易日
                    has_trading = any(prev_date < td < curr_date for td in trading_days)
                    if has_trading:
                        anomalies.append(
                            Anomaly(
                                date=str(curr_date),
                                code=code,
                                field="trade_date",
                                value=f"缺失 {gap} 天中的交易日",
                                expected="连续交易日",
                                severity="error",
                            )
                        )

    if anomalies:
        logger.warning("连续性检测发现 %d 个缺口", len(anomalies))
    return anomalies
