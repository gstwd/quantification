from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

# 增量拉取缓冲窗口（自然日）：起点回退该天数，保证边界 bar 的 prev_close/change_pct 可算
_INCREMENTAL_BUFFER_DAYS = 10


@dataclass
class IndexDailyBar:
    """指数日线行情数据结构（多数据源统一入库口径）。

    字段单位约定（与 index_daily_bar 表注释一致）：
    - volume：成交量，单位手
    - turnover：成交额，单位元
    各 SDK 客户端在转换时必须把上游单位归一化到此口径，
    保证无论最终采用哪个数据源，入库数据质量统一。
    """

    trade_date: date
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float
    turnover: float
    prev_close_price: float | None = None
    change_pct: float | None = None


def _parse_bar_date(value) -> date:
    """将上游日期的任意类型统一解析为 date。

    AkShare / efinance / pytdx 等各接口返回的日期列类型不一致
    （str / datetime / date / Timestamp），统一在此处收敛。

    Args:
        value: 上游日期单元格值。

    Returns:
        标准化后的 date。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


def incremental_start_date(since_date: date) -> date:
    """计算增量拉取的缓冲起始日。

    以 since_date 直接作为起点会丢失起点 bar 的 prev_close/change_pct，
    因此回退 _INCREMENTAL_BUFFER_DAYS 个自然日（覆盖周末与长假断档）。

    Args:
        since_date: 增量基准日（不含当日）。

    Returns:
        应传给上游接口的起始日期。
    """
    return since_date - timedelta(days=_INCREMENTAL_BUFFER_DAYS)


def index_code_market_prefix(index_code: str) -> str:
    """返回指数代码所属交易所前缀（sh/sz）。

    分类规则（与 AkShare 腾讯日线接口一致）：
    - 0 开头：上交所/中证系指数（上证综指、上证50、沪深300 等）
    - 51/56 开头：上交所指数（兼容历史命名）
    - 9 开头（930/931/932）：中证指数，归属上海市场
    - H 开头（H30xxx）：中证策略/主题指数（如 H30269 红利低波），归属上海市场
    - 其他（399 开头等）：深交所指数

    Args:
        index_code: 指数代码，如 000300、399001、931743、H30269

    Returns:
        'sh' 或 'sz'。
    """
    if index_code.startswith(("0", "51", "56", "9", "H", "h")):
        return "sh"
    return "sz"


def _index_code_to_market_symbol(index_code: str) -> str:
    """将 index_code 转为带交易所前缀的行情代码（如 sh000300、sz399001）。

    Args:
        index_code: 指数代码，如 000300、399001。

    Returns:
        带 sh/sz 前缀的代码，如 'sh000300'。
    """
    return f"{index_code_market_prefix(index_code)}{index_code}"


def _is_valid_price(value: float | None) -> bool:
    """判断价格是否有效（非空、非 NaN、为正数）。

    Args:
        value: 价格或成交量数值。

    Returns:
        True 表示有效。
    """
    if value is None:
        return False
    try:
        if math.isnan(value):
            return False
    except TypeError:
        return False
    return value > 0


def _ohlc_missing_ratio(bars: list[IndexDailyBar]) -> float:
    """计算日线列表中 OHLC 缺失比例（兼容旧口径，仅统计 open/high/low）。

    开盘/最高/最低任一为空、NaN 或非正值即视为该日 OHLC 不完整。
    新代码请优先使用 ohlc_missing_count / is_ohlc_complete（含收盘价）做严格校验。

    Args:
        bars: 日线列表（已按请求窗口过滤）。

    Returns:
        缺失比例（0.0~1.0），空列表返回 0.0。
    """
    if not bars:
        return 0.0
    missing = sum(
        1
        for b in bars
        if not (
            _is_valid_price(b.open_price)
            and _is_valid_price(b.high_price)
            and _is_valid_price(b.low_price)
        )
    )
    return missing / len(bars)


def ohlc_missing_count(bars: list[IndexDailyBar]) -> int:
    """统计请求窗口内 OHLC 四价不完整的交易日数量（严格口径）。

    开盘/最高/最低/收盘任一为空、NaN 或非正值即视为该日不合格；
    只要有一日缺失即整个数据源判定为不合格（多数据源切换依据）。

    Args:
        bars: 日线列表。

    Returns:
        缺失交易日数量（0 表示完全合格）。
    """
    return sum(
        1
        for b in bars
        if not (
            _is_valid_price(b.open_price)
            and _is_valid_price(b.high_price)
            and _is_valid_price(b.low_price)
            and _is_valid_price(b.close_price)
        )
    )


def is_ohlc_complete(bars: list[IndexDailyBar]) -> bool:
    """判断日线列表 OHLC 四价是否全部完整。

    Args:
        bars: 日线列表。

    Returns:
        所有 bar 的 OHLC 均有效时返回 True，空列表返回 True（无缺失可判定）。
    """
    return ohlc_missing_count(bars) == 0


def _build_index_bars(
    df,
    date_col: str,
    open_col: str,
    close_col: str,
    high_col: str,
    low_col: str,
    volume_col: str | None = None,
    amount_col: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[IndexDailyBar]:
    """将上游日线 DataFrame 统一转换为 IndexDailyBar 列表。

    集中处理各源差异：
    - 日期列类型与列名不同（腾讯/东财为英文列名，中证/通用为中文列名）；
    - 部分源缺少成交量或成交额列（腾讯无 volume、新浪无 amount），缺失列补 0；
    - 缺失数值统一转为 NaN（baostock 等源以空字符串表示缺失），
      由上游 OHLC 严格校验（ohlc_missing_count）判定数据源是否合格；
    - 新浪等源不支持服务端日期过滤，统一在本地按 start_date/end_date 过滤。

    Args:
        df: 上游返回的日线 DataFrame。
        date_col: 日期列名。
        open_col/close_col/high_col/low_col: OHLC 列名。
        volume_col: 成交量列名，None 表示该源无成交量（补 0）。
        amount_col: 成交额列名，None 表示该源无成交额（补 0）。
        start_date: 起始日 'YYYYMMDD'，本地过滤下限（含），None 不过滤。
        end_date: 结束日 'YYYYMMDD'，本地过滤上限（含），None 不过滤。

    Returns:
        按日期升序排列的日线列表。
    """
    if df is None or df.empty:
        return []

    dates = [_parse_bar_date(v) for v in df[date_col]]
    opens = [float(v) for v in pd.to_numeric(df[open_col], errors="coerce")]
    closes = [float(v) for v in pd.to_numeric(df[close_col], errors="coerce")]
    highs = [float(v) for v in pd.to_numeric(df[high_col], errors="coerce")]
    lows = [float(v) for v in pd.to_numeric(df[low_col], errors="coerce")]
    volumes = (
        [float(v) for v in pd.to_numeric(df[volume_col], errors="coerce").fillna(0.0)]
        if volume_col
        else [0.0] * len(df)
    )
    amounts = (
        [float(v) for v in pd.to_numeric(df[amount_col], errors="coerce").fillna(0.0)]
        if amount_col
        else [0.0] * len(df)
    )

    start = datetime.strptime(start_date, "%Y%m%d").date() if start_date else None
    end = datetime.strptime(end_date, "%Y%m%d").date() if end_date else None

    bars: list[IndexDailyBar] = []
    for i, bar_date in enumerate(dates):
        if start is not None and bar_date < start:
            continue
        if end is not None and bar_date > end:
            continue
        bars.append(
            IndexDailyBar(
                trade_date=bar_date,
                open_price=opens[i],
                close_price=closes[i],
                high_price=highs[i],
                low_price=lows[i],
                volume=volumes[i],
                turnover=amounts[i],
            )
        )
    # 逐日计算涨跌幅（第一根无前收盘，保持 None）
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close_price
        if prev_close and prev_close != 0:
            bars[i].prev_close_price = prev_close
            bars[i].change_pct = round((bars[i].close_price - prev_close) / prev_close * 100, 4)
    return bars


def compare_index_bar_overlap(
    bars_a: list[IndexDailyBar], bars_b: list[IndexDailyBar]
) -> tuple[int, float | None]:
    """对比两个数据源共同交易日的收盘点位一致性（数据质量核对）。

    指数点位由官方指数公司计算，不同源共同交易日收盘点位应基本一致；
    该函数用于多源切换时记录跨源差异，差异异常大时提示数据质量问题。

    Args:
        bars_a: 第一个数据源的日线列表（按日期升序）。
        bars_b: 第二个数据源的日线列表（按日期升序）。

    Returns:
        (共同交易日数量, 共同日收盘点位最大绝对差)；无共同日时返回 (0, None)。
    """
    close_b = {b.trade_date: b.close_price for b in bars_b}
    max_diff: float | None = None
    common = 0
    for b in bars_a:
        other = close_b.get(b.trade_date)
        if other is None or not _is_valid_price(b.close_price) or not _is_valid_price(other):
            continue
        common += 1
        diff = abs(b.close_price - other)
        if max_diff is None or diff > max_diff:
            max_diff = diff
    return common, max_diff
