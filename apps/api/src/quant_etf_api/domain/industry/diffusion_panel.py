"""扩散指标按行业分批计算的纯函数（供研究调试台使用，不依赖数据库）。

原全市场一次性物化的路径（P02）会在大区间内加载数千万行个股收盘，造成
内存飙升。本模块把计算切分为“单行业”：每次只加载该行业成员股的收盘行，
在交易日索引上重建窄表后计算扩散，并对缺失数据给出显式规则：

- 判涨要求 close_t 与 close_{t-lookback} 均非空，且 t 日股票归属该行业；
- 二者任一缺失（停牌/未上市/退市/数据缺口）当日不计入有效样本；
- 某行业某日有效样本为 0 时原始扩散为 NaN，20 日 MA 窗口内任意 NaN
  都会让该日平滑值为 NaN（与研报“无有效样本输出 NaN”口径一致）。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import numpy as np
import pandas as pd


def build_membership_chains(events: Iterable[Any]) -> dict[str, list[tuple[date, str]]]:
    """把成分事件流整理为“股票 → 按生效日升序的归属链”。

    Args:
        events: 可迭代的成分事件对象，需具备 stock_code / industry_code /
            start_date 属性（可为 ORM 行或 SimpleNamespace）。

    Returns:
        股票代码 → [(start_date, industry_code)] 升序列表。
    """
    chains: dict[str, list[tuple[date, str]]] = {}
    for event in events:
        chains.setdefault(event.stock_code, []).append(
            (event.start_date, event.industry_code)
        )
    for chain in chains.values():
        chain.sort(key=lambda item: item[0])
    return chains


def member_stock_codes(
    chains: dict[str, list[tuple[date, str]]],
    industry_code: str,
) -> list[str]:
    """返回曾在某行业出现过的全部股票代码（升序）。

    股票后续迁出行业仍保留在结果中，由归属矩阵按事件链在逐日判断剔除，
    避免“只看行业代码过滤事件”导致迁出后仍被误算为成员的偏差。

    Args:
        chains: build_membership_chains 的结果。
        industry_code: 一级行业代码。

    Returns:
        股票代码升序列表。
    """
    return sorted(
        stock
        for stock, chain in chains.items()
        if any(industry == industry_code for _, industry in chain)
    )


def _close_panel(
    close_rows: list[tuple[date, str, float]],
    trading_dates: list[date],
    stock_codes: list[str],
) -> pd.DataFrame:
    """把单行业个股收盘行转成 date × stock 窄表，并按交易日索引对齐。"""
    if not close_rows:
        return pd.DataFrame(
            index=pd.DatetimeIndex(trading_dates),
            columns=stock_codes,
            dtype=float,
        )
    frame = pd.DataFrame(close_rows, columns=["trade_date", "stock_code", "close"])
    panel = frame.pivot(index="trade_date", columns="stock_code", values="close")
    panel.index = pd.DatetimeIndex(panel.index)
    return panel.reindex(index=pd.DatetimeIndex(trading_dates), columns=stock_codes)


def _active_flags(
    chains: dict[str, list[tuple[date, str]]],
    stock_codes: list[str],
    trading_dates: list[date],
    industry_code: str,
) -> pd.DataFrame:
    """构造 date × stock 的当日归属标记（1.0=属于该行业，否则 NaN）。

    每只股票只做一次按日索引的 ffill（pandas C 层），避免对每个交易日
    做 Python 级逐股逐日判断，显著降低扩散大区间计算耗时。
    """
    index = pd.DatetimeIndex(trading_dates)
    flags = pd.DataFrame(np.nan, index=index, columns=stock_codes, dtype=float)
    if not chains or not stock_codes:
        return flags
    for stock in stock_codes:
        chain = chains.get(stock)
        if not chain:
            continue
        starts = pd.DatetimeIndex([start for start, _ in chain])
        codes = [code for _, code in chain]
        active = pd.Series(codes, index=starts).reindex(index, method="ffill")
        matched = (active == industry_code).where(active.notna())
        flags[stock] = matched.astype(float)
    return flags


def industry_diffusion_series(
    close_rows: Iterable[tuple[date, str, float]],
    trading_dates: list[date],
    chains: dict[str, list[tuple[date, str]]],
    industry_code: str,
    lookback: int,
    smooth_window: int,
    members: list[str] | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    """计算单个申万一级行业的数量占比扩散序列与每日样本统计。

    Args:
        close_rows: (trade_date, stock_code, close) 行，仅含该行业成员股。
        trading_dates: 市场交易日索引（升序），决定 shift(lookback) 的日历。
        chains: 全部行业的归属链（用于判断成员当日是否仍属于本行业）。
        industry_code: 待计算行业代码。
        lookback: 上涨判定回看交易日数。
        smooth_window: MA 平滑窗口。
        members: 该行业成员股列表；由调用方预计算时传入，避免重复扫描归属链。

    Returns:
        (扩散序列，按交易日索引的统计宽表)。统计宽表包含：
        member_count（当日归属成员数）、valid_count（当日有效样本数，
        即判涨所需两日收盘均存在的成员数）、raw_ratio（未平滑原始扩散）。
    """
    rows = [(d, code, float(close)) for d, code, close in close_rows if close is not None]
    if members is None:
        members = member_stock_codes(chains, industry_code)
    close = _close_panel(rows, trading_dates, members)
    membership = _active_flags(chains, members, trading_dates, industry_code)

    prev = close.shift(lookback)
    is_up = (close > prev).astype(float)
    is_up = is_up.where(~(close.isna() | prev.isna()))
    in_industry = membership == 1.0
    up_in_industry = is_up.where(in_industry)
    valid_counts = up_in_industry.notna().sum(axis=1)
    ups = up_in_industry.sum(axis=1, min_count=1)
    raw_ratio = ups / valid_counts.where(valid_counts > 0)
    smoothed = raw_ratio.rolling(window=smooth_window).mean()
    stats = pd.DataFrame(
        {
            "member_count": in_industry.sum(axis=1).astype(int),
            "valid_count": valid_counts.astype(int),
            "raw_ratio": raw_ratio,
        },
        index=close.index,
    )
    return smoothed.rename(industry_code), stats
