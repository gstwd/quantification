"""Deflated Sharpe 门槛校准（纯函数模块）。

优化验收第 8 项用「候选 − 基线」逐日超额净收益的 Deflated Sharpe 判定改进是否
显著，门槛缺省 0.95。DSR 公式本身建立在独立同分布、试验相互独立的近似上，而真实
的超额序列有两个特点会让名义门槛偏离实际假阳性率：

- 稀疏：候选与基线大多数交易日持仓相同，超额为 0，只有少数"差异日"非零，峰度极高；
- 成簇：差异日集中在若干事件里（自相关），iid 公式会低估夏普估计的波动。

本模块用零假设模拟回答两个问题：

1. 假阳性率：把超额序列的均值抹成 0（真实改进为零），按循环块重采样保留成簇结构，
   看各门槛下有多少比例仍然通过；多条序列（同一批次的变体）时联合重采样、每次挑出
   夏普最高者再检验，模拟"从相关候选中挑赢家"的选择偏差；
2. 功效：在零假设样本的差异日上注入已知的年化超额夏普，看各门槛下的通过率。

均值只在差异日上扣除、漂移只在差异日上注入，保持"非差异日超额恒为 0"的结构。
DSR 的批量计算与 ``robustness.deflated_sharpe_ratio`` 逐项同式，单测保证二者一致。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from quant_etf_api.domain.research.robustness import (
    DEFAULT_TRADING_DAYS_PER_YEAR,
    EULER_GAMMA,
    _norm_cdf,
    _norm_ppf,
    skewness_and_kurtosis,
)

# 缺省检验的门槛档位（含当前验收缺省 0.95）
DEFAULT_THRESHOLDS: tuple[float, ...] = (0.90, 0.95, 0.975, 0.99)
# 缺省注入的年化超额夏普（功效曲线横轴）
DEFAULT_INJECTED_SHARPES: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
# 判定"差异日"的绝对值下限：低于该值视为两侧收益相同
_ACTIVE_EPS = 1e-12
# 多序列联合重采样时每批模拟次数，限制 (批次, 序列, 交易日) 三维数组的内存
_SIM_CHUNK = 64


@dataclass(frozen=True)
class SeriesProfile:
    """超额序列的结构画像。

    Attributes:
        n_observations: 交易日数。
        active_days: 差异日数（超额非零）。
        sharpe_annualized: 年化夏普。
        skewness: 偏度。
        kurtosis: 峰度（正态为 3）。
    """

    n_observations: int
    active_days: int
    sharpe_annualized: float
    skewness: float
    kurtosis: float

    def to_payload(self) -> dict[str, Any]:
        """转为可序列化字典。"""
        return {
            "n_observations": self.n_observations,
            "active_days": self.active_days,
            "sharpe_annualized": round(self.sharpe_annualized, 4),
            "skewness": round(self.skewness, 4),
            "kurtosis": round(self.kurtosis, 4),
        }


@dataclass(frozen=True)
class NullCalibration:
    """零假设模拟结果（假阳性率）。

    Attributes:
        mode: ``single``=单条序列直接检验；``selected``=多条序列每次挑夏普最高者再检验。
        n_series: 参与模拟的序列数。
        n_trials: DSR 使用的试验次数 N。
        n_observations: 每条序列的交易日数。
        n_sims: 模拟次数。
        block: 循环块长度（交易日）。
        pass_rates: 门槛 → 通过率（即假阳性率）。
        dsr_quantiles: 模拟 DSR 的分位数（p50/p90/p95/p99）。
    """

    mode: str
    n_series: int
    n_trials: int
    n_observations: int
    n_sims: int
    block: int
    pass_rates: dict[float, float]
    dsr_quantiles: dict[str, float] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """转为可序列化字典（门槛键转字符串）。"""
        return {
            "mode": self.mode,
            "n_series": self.n_series,
            "n_trials": self.n_trials,
            "n_observations": self.n_observations,
            "n_sims": self.n_sims,
            "block": self.block,
            "pass_rates": {f"{k:g}": round(v, 4) for k, v in self.pass_rates.items()},
            "dsr_quantiles": {k: round(v, 4) for k, v in self.dsr_quantiles.items()},
        }


@dataclass(frozen=True)
class PowerPoint:
    """功效曲线上的一个点。

    Attributes:
        injected_sharpe: 注入的年化超额夏普。
        pass_rates: 门槛 → 通过率。
    """

    injected_sharpe: float
    pass_rates: dict[float, float]

    def to_payload(self) -> dict[str, Any]:
        """转为可序列化字典。"""
        return {
            "injected_sharpe": self.injected_sharpe,
            "pass_rates": {f"{k:g}": round(v, 4) for k, v in self.pass_rates.items()},
        }


def series_profile(
    excess: Sequence[float], trading_days: int = DEFAULT_TRADING_DAYS_PER_YEAR
) -> SeriesProfile:
    """计算超额序列的结构画像。

    Args:
        excess: 逐日超额收益（小数口径）。
        trading_days: 年化系数。

    Returns:
        SeriesProfile。
    """
    values = np.asarray(excess, dtype=float)
    n = int(values.size)
    active = int(np.count_nonzero(np.abs(values) > _ACTIVE_EPS))
    std = float(values.std()) if n else 0.0
    sharpe = float(values.mean() / std * math.sqrt(trading_days)) if std > 0 else 0.0
    skew, kurt = skewness_and_kurtosis(list(values))
    return SeriesProfile(n, active, sharpe, skew, kurt)


def deflated_sharpe_batch(matrix: np.ndarray, n_trials: int) -> np.ndarray:
    """按行批量计算 Deflated Sharpe（与 ``deflated_sharpe_ratio`` 同式）。

    Args:
        matrix: 二维数组，每行一条日收益序列（小数口径）。
        n_trials: 试验次数 N，必须 ≥ 1。

    Returns:
        每行的 DSR（0-1）；样本不足或零波动的行为 0。

    Raises:
        ValueError: n_trials < 1 时抛出。
    """
    if n_trials < 1:
        raise ValueError("试验次数必须 >= 1")
    rows = np.atleast_2d(np.asarray(matrix, dtype=float))
    n = rows.shape[1]
    result = np.zeros(rows.shape[0])
    if n < 3:
        return result
    mean = rows.mean(axis=1)
    centered = rows - mean[:, None]
    variance = (centered**2).mean(axis=1)
    std = np.sqrt(variance)
    valid = std > 0
    if not valid.any():
        return result
    safe_std = np.where(valid, std, 1.0)
    sr = mean / safe_std
    skew = (centered**3).mean(axis=1) / safe_std**3
    kurt = (centered**4).mean(axis=1) / safe_std**4
    se = np.sqrt((1 + 0.5 * sr**2) / max(1, n - 1))
    if n_trials > 1:
        quantile_term = (1 - EULER_GAMMA) * _norm_ppf(1 - 1 / n_trials) + EULER_GAMMA * _norm_ppf(
            1 - 1 / (n_trials * math.e)
        )
    else:
        quantile_term = 0.0
    expected_max = se * quantile_term
    denom = np.sqrt(np.maximum(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr**2))
    z = (sr - expected_max) * math.sqrt(n - 1) / denom
    dsr = np.array([_norm_cdf(float(value)) for value in z])
    return np.where(valid, dsr, 0.0)


def _active_demean(values: np.ndarray) -> np.ndarray:
    """把序列均值抹成 0，只在差异日上扣除（非差异日保持 0）。

    Args:
        values: 一维或二维（每行一条序列）数组。

    Returns:
        同形数组。
    """
    rows = np.atleast_2d(values).astype(float)
    out = rows.copy()
    for i, row in enumerate(rows):
        mask = np.abs(row) > _ACTIVE_EPS
        count = int(mask.sum())
        if count:
            out[i, mask] = row[mask] - row.sum() / count
    return out if values.ndim == 2 else out[0]


def circular_block_indices(n: int, block: int, n_sims: int, rng: np.random.Generator) -> np.ndarray:
    """生成循环块重采样的下标矩阵。

    Args:
        n: 序列长度。
        block: 块长度（交易日）。
        n_sims: 模拟次数。
        rng: numpy 随机数生成器。

    Returns:
        形如 (n_sims, n) 的整数下标矩阵。

    Raises:
        ValueError: block < 1 或 n < 1 时抛出。
    """
    if block < 1 or n < 1:
        raise ValueError("块长度与序列长度必须 >= 1")
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(n_sims, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return idx.reshape(n_sims, n_blocks * block)[:, :n]


def _rates(dsr: np.ndarray, thresholds: Sequence[float]) -> dict[float, float]:
    """统计各门槛下的通过比例。"""
    total = max(1, int(dsr.size))
    return {float(t): float(np.count_nonzero(dsr >= t)) / total for t in thresholds}


def _quantiles(dsr: np.ndarray) -> dict[str, float]:
    """模拟 DSR 的分位数摘要。"""
    if dsr.size == 0:
        return {}
    return {f"p{q}": float(np.quantile(dsr, q / 100)) for q in (50, 90, 95, 99)}


def null_pass_rates(
    series: Sequence[Sequence[float]],
    n_trials: int,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    *,
    n_sims: int = 1000,
    block: int = 20,
    seed: int = 20261024,
) -> NullCalibration:
    """零假设下各门槛的通过率（假阳性率）。

    一条序列：去均值后重采样，直接按 N 检验（"候选已定、只看运气"的情形）。
    多条序列（等长、按同一交易日轴对齐）：联合重采样保留序列间相关，每次挑年化
    夏普最高者再按 N 检验（"从相关候选里挑赢家"的情形）。

    Args:
        series: 一条或多条等长超额序列（小数口径）。
        n_trials: DSR 使用的 N。
        thresholds: 待检验的门槛。
        n_sims: 模拟次数。
        block: 循环块长度（交易日）。
        seed: 随机种子（固定以保证可复现）。

    Returns:
        NullCalibration。

    Raises:
        ValueError: 序列为空、长度不一致或模拟次数 < 1 时抛出。
    """
    if n_sims < 1:
        raise ValueError("模拟次数必须 >= 1")
    matrix = np.asarray([list(s) for s in series], dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise ValueError("至少需要一条非空、等长的超额序列")
    k, n = matrix.shape
    centered = _active_demean(matrix)
    rng = np.random.default_rng(seed)
    selected_rows: list[np.ndarray] = []
    done = 0
    while done < n_sims:
        size = min(_SIM_CHUNK, n_sims - done)
        idx = circular_block_indices(n, block, size, rng)
        # (size, k, n)：同一组下标作用于全部序列，保留同日相关结构
        sampled = centered[:, idx].transpose(1, 0, 2)
        if k == 1:
            selected_rows.append(sampled[:, 0, :])
        else:
            std = sampled.std(axis=2)
            sharpe = np.where(std > 0, sampled.mean(axis=2) / np.where(std > 0, std, 1.0), -np.inf)
            best = sharpe.argmax(axis=1)
            selected_rows.append(sampled[np.arange(size), best, :])
        done += size
    selected = np.concatenate(selected_rows, axis=0)
    dsr = deflated_sharpe_batch(selected, n_trials)
    return NullCalibration(
        mode="single" if k == 1 else "selected",
        n_series=k,
        n_trials=n_trials,
        n_observations=n,
        n_sims=n_sims,
        block=block,
        pass_rates=_rates(dsr, thresholds),
        dsr_quantiles=_quantiles(dsr),
    )


def power_curve(
    excess: Sequence[float],
    n_trials: int,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    injected_sharpes: Sequence[float] = DEFAULT_INJECTED_SHARPES,
    *,
    n_sims: int = 500,
    block: int = 20,
    seed: int = 20261024,
    trading_days: int = DEFAULT_TRADING_DAYS_PER_YEAR,
) -> list[PowerPoint]:
    """在零假设样本上注入已知超额夏普，统计各门槛的通过率（功效）。

    漂移只加在差异日上：真实改进只能体现在候选与基线持仓不同的日子。漂移大小
    按原序列（去均值后）全样本波动折算，使注入后全样本年化夏普约等于目标值。

    Args:
        excess: 超额序列（小数口径）。
        n_trials: DSR 使用的 N。
        thresholds: 待检验的门槛。
        injected_sharpes: 注入的年化超额夏普列表。
        n_sims: 每个注入值的模拟次数。
        block: 循环块长度（交易日）。
        seed: 随机种子。
        trading_days: 年化系数。

    Returns:
        PowerPoint 列表（与 injected_sharpes 同序）；序列无差异日时各点通过率为 0。

    Raises:
        ValueError: 序列为空或模拟次数 < 1 时抛出。
    """
    if n_sims < 1:
        raise ValueError("模拟次数必须 >= 1")
    values = np.asarray(list(excess), dtype=float)
    if values.size == 0:
        raise ValueError("超额序列为空")
    n = values.size
    centered = _active_demean(values)
    std = float(centered.std())
    rng = np.random.default_rng(seed)
    idx = circular_block_indices(n, block, n_sims, rng)
    sampled = centered[idx]
    active_mask = np.abs(values[idx]) > _ACTIVE_EPS
    active_counts = active_mask.sum(axis=1)
    points: list[PowerPoint] = []
    for target in injected_sharpes:
        if std <= 0:
            points.append(PowerPoint(float(target), {float(t): 0.0 for t in thresholds}))
            continue
        total_drift = float(target) / math.sqrt(trading_days) * std * n
        per_day = np.where(active_counts > 0, total_drift / np.maximum(active_counts, 1), 0.0)
        shifted = sampled + active_mask * per_day[:, None]
        dsr = deflated_sharpe_batch(shifted, n_trials)
        points.append(PowerPoint(float(target), _rates(dsr, thresholds)))
    return points


def synthetic_sparse_excess(
    n_days: int = 2430,
    *,
    active_ratio: float = 0.08,
    episode_len: int = 8,
    daily_vol: float = 0.01,
    tail_df: float = 4.0,
    n_series: int = 1,
    correlation: float = 0.7,
    seed: int = 20261024,
) -> list[list[float]]:
    """生成稀疏、成簇、厚尾的合成超额序列（均值为 0）。

    差异日按若干"事件"成簇出现（几何分布长度），事件内取 t 分布厚尾收益；
    多条序列共享同一组事件，事件内收益按给定相关系数混合共同成分与独立成分，
    模拟同一批次的相关变体。

    Args:
        n_days: 交易日数。
        active_ratio: 差异日占比（0-1）。
        episode_len: 事件平均长度（交易日）。
        daily_vol: 差异日收益的标准差（小数口径）。
        tail_df: t 分布自由度（越小尾部越厚，必须 > 2）。
        n_series: 序列条数。
        correlation: 多条序列在差异日上的相关系数（0-1）。
        seed: 随机种子。

    Returns:
        n_series 条等长序列。

    Raises:
        ValueError: 参数越界时抛出。
    """
    if n_days < 3 or not 0 < active_ratio <= 1 or episode_len < 1 or tail_df <= 2:
        raise ValueError("合成序列参数越界")
    if n_series < 1 or not 0 <= correlation <= 1:
        raise ValueError("序列条数必须 >= 1，相关系数必须在 [0, 1]")
    rng = np.random.default_rng(seed)
    mask = np.zeros(n_days, dtype=bool)
    target_active = max(1, int(round(n_days * active_ratio)))
    guard = 0
    while int(mask.sum()) < target_active and guard < n_days * 10:
        start = int(rng.integers(0, n_days))
        length = int(rng.geometric(1 / episode_len))
        mask[start : min(n_days, start + length)] = True
        guard += 1
    active_idx = np.flatnonzero(mask)
    # t 分布方差为 df/(df-2)，按此缩放到目标波动
    scale = daily_vol / math.sqrt(tail_df / (tail_df - 2))
    common = rng.standard_t(tail_df, size=active_idx.size) * scale
    out: list[list[float]] = []
    for _ in range(n_series):
        idio = rng.standard_t(tail_df, size=active_idx.size) * scale
        mixed = math.sqrt(correlation) * common + math.sqrt(1 - correlation) * idio
        row = np.zeros(n_days)
        row[active_idx] = mixed - mixed.mean()
        out.append(row.tolist())
    return out
