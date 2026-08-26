"""宏观指标 period 字符串解析与时点化过滤工具。

macro_indicator 表的 period 字段是字符串：LPR 形如 'YYYY-MM-DD'（报价日），
CPI/PMI 等月度指标形如 'YYYY-MM'（当月）。因子层需要按"截止 trade_date
的最近一期"取值，且回测/补算历史日期时不能使用 trade_date 之后才公布的
记录（前视偏差）。本模块提供统一的解析与过滤入口。
"""

from __future__ import annotations

from datetime import date, datetime


def parse_macro_period(period: str) -> date | None:
    """将宏观指标 period 字符串解析为日期。

    优先按 'YYYY-MM-DD'（LPR 报价日）解析，其次兼容带时间部分
    （'YYYY-MM-DD HH:MM:SS'），最后按 'YYYY-MM'（月度指标，统一取当月首日）。
    解析失败返回 None，调用方应跳过该记录。

    Args:
        period: 宏观指标 period 字符串，如 '2024-07-22' 或 '2024-07'。

    Returns:
        解析后的日期；格式无法识别时返回 None。
    """
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m"):
        try:
            return datetime.strptime(period, fmt).date()
        except ValueError:
            continue
    return None


def latest_value_as_of(
    period_values: dict[str, float],
    trade_date: date,
) -> tuple[str, float] | None:
    """返回截止 trade_date 的最近一期记录 (period, value)。

    仅考虑 period 解析日期 <= trade_date 的记录，选择 period 日期最大的一条；
    避免把数值最大（通常是最老、最高）的一期误当作"最新一期"。

    Args:
        period_values: period → 数值 的映射，如 {'2019-08-20': 4.35}。
        trade_date: 目标交易日。

    Returns:
        (period, value) 元组；无有效记录时返回 None。
    """
    parsed: list[tuple[date, str, float]] = []
    for period, value in period_values.items():
        period_date = parse_macro_period(period)
        if period_date is not None and period_date <= trade_date:
            parsed.append((period_date, period, value))
    if not parsed:
        return None
    _, latest_period, latest_value = max(parsed, key=lambda item: item[0])
    return latest_period, latest_value


def macro_indicators_as_of(
    all_macro: dict[str, dict[str, float]] | None,
    trade_date: date,
) -> dict[str, dict[str, float]]:
    """构建截止 trade_date 的时点化宏观指标视图。

    仅保留 period 解析日期 <= trade_date 的记录，避免回测历史日期使用
    回测区间内未来才公布的宏观数据（前视偏差）。

    Args:
        all_macro: 全量宏观指标映射，key=indicator_code，value={period: value}。
        trade_date: 目标交易日。

    Returns:
        过滤后的映射，结构与原映射一致。
    """
    result: dict[str, dict[str, float]] = {}
    for code, periods in (all_macro or {}).items():
        filtered: dict[str, float] = {}
        for period, value in periods.items():
            period_date = parse_macro_period(period)
            if period_date is not None and period_date <= trade_date:
                filtered[period] = value
        result[code] = filtered
    return result
