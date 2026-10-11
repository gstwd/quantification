"""宏观指标 period 字符串解析与时点化过滤工具。

macro_indicator 表的 period 字段是字符串：LPR 形如 'YYYY-MM-DD'（报价日），
CPI/PMI 等月度指标形如 'YYYY-MM'（当月）。因子层需要按"截止 trade_date
已公布的最近一期"取值，且回测/补算历史日期时不能使用 trade_date 之后才公布的
记录（前视偏差）。月度指标的 period 是参考月而非发布日，必须叠加发布滞后
（``macro_available_date``）。本模块提供统一的解析与过滤入口。
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


# 月度指标的发布滞后：参考月数据在「次月第 N 日」起才可见。
# 统计局制造业 PMI 于参考月最后一天发布，次月 1 日起可用；
# CPI 于次月 9-15 日发布，保守取次月 15 日。未列出的指标（如 LPR 报价日）按期日本身可见。
_MONTHLY_RELEASE_DAY: dict[str, int] = {"pmi": 1, "cpi": 15}


def macro_available_date(indicator_code: str | None, period: str) -> date | None:
    """返回宏观指标某一期记录在交易中可被使用的最早日期（发布可见日）。

    月度指标的 period 在库中记为参考月首日（或 'YYYY-MM'），直接拿它与交易日比较
    会让参考月当月就看到尚未公布的数据（前视偏差）；这里按指标代码叠加发布滞后。

    Args:
        indicator_code: 指标代码，如 pmi / cpi / lpr1y；None 表示不叠加滞后。
        period: period 字符串。

    Returns:
        可见日；period 无法解析时返回 None。
    """
    period_date = parse_macro_period(period)
    if period_date is None:
        return None
    release_day = _MONTHLY_RELEASE_DAY.get(indicator_code or "")
    if release_day is None:
        return period_date
    if period_date.month == 12:
        return date(period_date.year + 1, 1, release_day)
    return date(period_date.year, period_date.month + 1, release_day)


def latest_value_as_of(
    period_values: dict[str, float],
    trade_date: date,
    indicator_code: str | None = None,
) -> tuple[str, float] | None:
    """返回截止 trade_date 已公布的最近一期记录 (period, value)。

    仅考虑可见日 <= trade_date 的记录，选择参考期日期最大的一条；
    避免把数值最大（通常是最老、最高）的一期误当作"最新一期"。

    Args:
        period_values: period → 数值 的映射，如 {'2019-08-20': 4.35}。
        trade_date: 目标交易日。
        indicator_code: 指标代码，用于叠加月度发布滞后；None 表示按期日本身可见。

    Returns:
        (period, value) 元组；无有效记录时返回 None。
    """
    parsed: list[tuple[date, str, float]] = []
    for period, value in period_values.items():
        period_date = parse_macro_period(period)
        available = macro_available_date(indicator_code, period)
        if period_date is not None and available is not None and available <= trade_date:
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

    仅保留发布可见日 <= trade_date 的记录（月度指标叠加发布滞后），避免回测
    历史日期使用回测区间内未来才公布的宏观数据（前视偏差）。

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
            available = macro_available_date(code, period)
            if available is not None and available <= trade_date:
                filtered[period] = value
        result[code] = filtered
    return result
