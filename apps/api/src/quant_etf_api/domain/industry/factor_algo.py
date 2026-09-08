"""行业轮动纯算法层：RRG、四象限与数量占比扩散指标。

复现西部证券《指数化配置系列研究（6）》与 docs/research 复现工程的口径：
- RRG 采用 Julius de Kempenaer 比率法，中枢 100；
- 扩散指标仅实现"数量占比"版（不做市值加权）；
- 所有函数为 DataFrame in / DataFrame out 的纯函数，不依赖数据库。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _smooth_ratio_shift(x: pd.DataFrame, lookback: int, smooth: int) -> pd.DataFrame:
    """共享算子：``100 * x / x.shift(lookback)`` 后再做 smooth 日 MA。

    RS-Ratio 与 RS-Momentum 复用同一算子，保证核心公式只落地一处。

    Args:
        x: 宽表数据，index=date，columns=行业代码。
        lookback: 比率回看交易日数。
        smooth: MA 平滑窗口。

    Returns:
        平滑后的比率宽表。
    """
    # 比较窗口跨越任一缺口即不可比；否则缺口后数日会错误地与缺口前数据相除。
    complete = (
        x.notna()
        .all(axis=1)
        .rolling(window=lookback + 1, min_periods=lookback + 1)
        .sum()
        .eq(lookback + 1)
    )
    raw = (100.0 * x / x.shift(lookback)).where(complete, axis=0)
    return raw.rolling(window=smooth, min_periods=smooth).mean()


def equal_weight_benchmark(price: pd.DataFrame) -> pd.Series:
    """构造行业等权组合净值（首日=1.0）作为 RRG 基准。

    研报以中信一级行业等权组合为 RRG 基准；本系统以申万一级行业等权组合
    代替。等权组合 = 各行业日收益等权平均后累乘，而非截面点位算术均值。

    Args:
        price: 行业收盘价宽表，index=date，columns=industry_code。

    Returns:
        等权组合 NAV Series，index=price.index。
    """
    # 行业缺口不能通过跳过该行业重配基准，否则同一基准在不同日期的成分不一致。
    valid = price.notna().all(axis=1)
    result = pd.Series(np.nan, index=price.index, dtype=float)
    previous: pd.Series | None = None
    nav = 1.0
    for trade_date, row in price.iterrows():
        if not bool(valid.loc[trade_date]):
            previous = None
            continue
        if previous is None:
            # 缺口后的首个完整日重新归一化；跨缺口的 RRG 窗口仍会保持 NaN。
            nav = 1.0
        else:
            nav *= 1.0 + float((row / previous - 1.0).mean())
        result.loc[trade_date] = nav
        previous = row
    return result


def compute_rs(price: pd.DataFrame, benchmark: pd.Series) -> pd.DataFrame:
    """计算相对强度 RS = price / benchmark * 100。

    price 会被 reindex 到 benchmark.index。行业日线或基准任一缺失时严格
    返回 NaN，不做 ffill，也不通过动态剔除行业改变基准成分。

    Args:
        price: 行业收盘价宽表，index=date，columns=industry_code。
        benchmark: 基准价格 Series，index=date。

    Returns:
        RS 宽表，index=benchmark.index，columns=price.columns。
    """
    aligned = price.reindex(benchmark.index)
    complete_row = aligned.notna().all(axis=1) & benchmark.notna()
    return aligned.div(benchmark, axis=0).where(complete_row, axis=0) * 100.0


def _require_min_len(price: pd.DataFrame, benchmark: pd.Series, min_len: int) -> None:
    """校验输入行数不少于 warm-up 下界，不足时抛 ValueError。"""
    if price.shape[0] < min_len or benchmark.shape[0] < min_len:
        raise ValueError(
            f"数据长度不足：需要 >= {min_len} 行，"
            f"实得 price={price.shape[0]}, benchmark={benchmark.shape[0]}"
        )


def compute_rs_ratio(
    price: pd.DataFrame,
    benchmark: pd.Series,
    lookback: int = 220,
    smooth_window: int = 20,
) -> pd.DataFrame:
    """计算 JdK RS-Ratio。

    Args:
        price: 行业收盘价宽表。
        benchmark: 基准价格 Series。
        lookback: 比率回看交易日数，默认 220。
        smooth_window: MA 平滑窗口，默认 20。

    Returns:
        RS-Ratio 宽表；起始 lookback + smooth_window - 1 行为 NaN。
    """
    _require_min_len(price, benchmark, lookback + smooth_window)
    rs = compute_rs(price, benchmark)
    return _smooth_ratio_shift(rs, lookback, smooth_window)


def compute_rs_momentum(
    price: pd.DataFrame,
    benchmark: pd.Series,
    lookback_ratio: int = 220,
    lookback_mom: int = 60,
    smooth_window: int = 20,
) -> pd.DataFrame:
    """计算 JdK RS-Momentum。

    Args:
        price: 行业收盘价宽表。
        benchmark: 基准价格 Series。
        lookback_ratio: RS-Ratio 回看天数，默认 220。
        lookback_mom: RS-Ratio 的比率回看天数，默认 60。
        smooth_window: MA 平滑窗口，默认 20。

    Returns:
        RS-Momentum 宽表；起始
        lookback_ratio + lookback_mom + 2*(smooth_window-1) 行为 NaN。
    """
    min_len = lookback_ratio + lookback_mom + 2 * (smooth_window - 1) + 1
    _require_min_len(price, benchmark, min_len)
    rs_ratio = compute_rs_ratio(
        price, benchmark, lookback=lookback_ratio, smooth_window=smooth_window
    )
    return _smooth_ratio_shift(rs_ratio, lookback_mom, smooth_window)


def compute_rrg(
    price: pd.DataFrame,
    benchmark: pd.Series,
    lookback_ratio: int = 220,
    lookback_mom: int = 60,
    smooth_window: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """一次计算 (RS-Ratio, RS-Momentum)，内部复用中间量。

    Args:
        price: 行业收盘价宽表。
        benchmark: 基准价格 Series。
        lookback_ratio: RS 比率回看天数，默认 220。
        lookback_mom: RS-Ratio 比率回看天数，默认 60。
        smooth_window: MA 平滑窗口，默认 20。

    Returns:
        (rs_ratio, rs_momentum) 宽表元组。
    """
    min_len = lookback_ratio + lookback_mom + 2 * (smooth_window - 1) + 1
    _require_min_len(price, benchmark, min_len)
    overlap = price.index.intersection(benchmark.index)
    if overlap.shape[0] < min_len:
        raise ValueError(
            f"price 与 benchmark 日历重合不足 {overlap.shape[0]} 行"
            f"（需要 >= {min_len}），请检查数据完整性"
        )
    rs = compute_rs(price, benchmark)
    rs_ratio = _smooth_ratio_shift(rs, lookback_ratio, smooth_window)
    rs_momentum = _smooth_ratio_shift(rs_ratio, lookback_mom, smooth_window)
    return rs_ratio, rs_momentum


def classify_quadrant(rs_ratio: pd.DataFrame, rs_momentum: pd.DataFrame) -> pd.DataFrame:
    """将 (RS-Ratio, RS-Momentum) 分类为 1/2/3/4 象限。

    - 1 = 领先（两者 > 100）；2 = 改善（Ratio<100, Mom>100）；
    - 3 = 滞后（两者 < 100）；4 = 疲软（Ratio>100, Mom<100）。
    任一值为 NaN 或恰为 100 时返回 NaN，不强行归边。

    Args:
        rs_ratio: RS-Ratio 宽表。
        rs_momentum: RS-Momentum 宽表，列须与 rs_ratio 一致。

    Returns:
        同形状的 float 宽表，值 ∈ {1,2,3,4,NaN}。
    """
    if not rs_ratio.columns.equals(rs_momentum.columns):
        raise ValueError("rs_ratio 与 rs_momentum 的列必须一致")
    aligned_r, aligned_m = rs_ratio.align(rs_momentum, axis=0)
    result = pd.DataFrame(np.nan, index=aligned_r.index, columns=aligned_r.columns)
    result[(aligned_r > 100) & (aligned_m > 100)] = 1.0
    result[(aligned_r < 100) & (aligned_m > 100)] = 2.0
    result[(aligned_r < 100) & (aligned_m < 100)] = 3.0
    result[(aligned_r > 100) & (aligned_m < 100)] = 4.0
    return result


def _up_flags(close: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """上涨判定矩阵：close > lookback 日前收盘，缺值位置置 NaN。"""
    prev = close.shift(lookback)
    is_up = (close > prev).astype(float)
    return is_up.where(~(close.isna() | prev.isna()))


def diffusion_count_ratio(
    close: pd.DataFrame,
    membership: pd.DataFrame,
    lookback: int = 220,
    smooth_window: int = 20,
) -> pd.DataFrame:
    """计算数量占比版行业扩散指标。

    扩散 = MA_smooth(行业内上涨成分股数 / 有效成分股数)；上涨判定为
    close_t > close_{t-lookback}。某行业某日全员无效样本时返回 NaN。

    Args:
        close: 个股收盘价宽表，index=date，columns=stock_code。
        membership: 时变成分归属宽表，index=date，columns=stock_code，
            value=industry_code 或 NaN。
        lookback: 上涨判定回看交易日数，默认 220。
        smooth_window: MA 平滑窗口，默认 20。

    Returns:
        扩散指标宽表，index=date，columns=industry_code，值域 [0,1]。
    """
    aligned_membership = membership.reindex(index=close.index, columns=close.columns)
    is_up = _up_flags(close, lookback)
    industries = sorted(
        {v for v in pd.unique(aligned_membership.values.ravel()) if pd.notna(v)},
        key=str,
    )
    columns: dict[str, pd.Series] = {}
    for industry in industries:
        in_ind = aligned_membership == industry
        up_in_ind = is_up.where(in_ind)
        ups = up_in_ind.sum(axis=1, min_count=1)
        valid = up_in_ind.notna().sum(axis=1)
        columns[str(industry)] = ups / valid.where(valid > 0)
    diffusion = pd.DataFrame(columns, index=close.index)
    return diffusion.rolling(window=smooth_window).mean()


def distance_from_center(
    rs_ratio: pd.DataFrame, rs_momentum: pd.DataFrame, center: float = 100.0
) -> pd.DataFrame:
    """计算各行业到 RRG 中心 (center, center) 的欧氏距离。"""
    return np.sqrt((rs_ratio - center) ** 2 + (rs_momentum - center) ** 2)


def select_by_quadrant(
    rs_ratio: pd.DataFrame,
    rs_momentum: pd.DataFrame,
    quadrants: tuple[int, ...] = (1, 2),
    top_n: int = 6,
) -> pd.DataFrame:
    """信号 A：保留指定象限内行业，超 top_n 时按距中心距离取最远 top_n。"""
    quad = classify_quadrant(rs_ratio, rs_momentum)
    in_quad = quad.isin(list(quadrants))
    dist = distance_from_center(rs_ratio, rs_momentum).where(in_quad)
    ranks = dist.rank(axis=1, ascending=False, method="first")
    return in_quad & ranks.le(top_n)


def select_by_diffusion(diffusion: pd.DataFrame, top_n: int = 6) -> pd.DataFrame:
    """信号 B：每日扩散指标 top_n，warm-up NaN 当日不选。"""
    ranks = diffusion.rank(axis=1, ascending=False, method="first")
    return ranks.le(top_n)


def select_diffusion_with_rrg(
    diffusion: pd.DataFrame,
    rs_ratio: pd.DataFrame,
    rs_momentum: pd.DataFrame,
    top_n: int = 6,
    keep_quadrants: tuple[int, ...] = (1, 2),
) -> pd.DataFrame:
    """信号 C：扩散 top_n 先选，再剔除非 keep_quadrants 象限行业，不补足。"""
    if not diffusion.columns.equals(rs_ratio.columns):
        raise ValueError("diffusion 与 rs_ratio 的列必须一致")
    by_diffusion = select_by_diffusion(diffusion, top_n=top_n)
    quad = classify_quadrant(rs_ratio, rs_momentum)
    keep = quad.isin(list(keep_quadrants))
    return by_diffusion & keep
