"""候选集级别的过拟合风险统计（纯函数模块）。

与 ``domain.research.stability`` 的分工：稳健性模块只描述"单次回测自身的
结构特征"，本模块回答"这组候选里挑出的赢家有多少是运气"，因此必须知道
候选集合与试验次数。

实现的统计量与量化研究方法论文档第七条一致：
- CSCV-PBO：样本内最优候选在样本外落到中位数以下的概率；
- Deflated Sharpe：计入多重检验后夏普仍然显著的概率；
- 块自助法：年化夏普的置信区间（保留收益序列的自相关结构）；
- 邻域稳定度：单旋钮扰动下指标是否同向、是否出现方向反转。

全部为纯函数，无外部依赖（不使用 scipy），便于单测与复用。
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass

DEFAULT_TRADING_DAYS_PER_YEAR = 252
# 欧拉-马歇罗尼常数，Deflated Sharpe 期望最大值公式使用
EULER_GAMMA = 0.5772156649015329
# CSCV 需要的最少分块数：低于该值时对称切分方式太少（2 块只有 1 种切分），
# PBO 恒为 0，读起来像"没有过拟合"却是纯粹的假阳性保护
MIN_PBO_BLOCKS = 4


@dataclass
class PboResult:
    """CSCV-PBO 估计结果。

    Attributes:
        pbo: 回测过拟合概率（0-1，约 0.5 相当于纯噪声）；分块不足时为 None。
        n_candidates: 参与计算的候选数量。
        n_splits: 对称切分数量（C(S, S/2)）。
        n_blocks: 分块数量。
        reason: 未给出 pbo 时的原因说明（分块不足等）。
    """

    pbo: float | None
    n_candidates: int
    n_splits: int
    n_blocks: int
    reason: str | None = None


@dataclass
class DeflatedSharpeResult:
    """Deflated Sharpe 估计结果。

    Attributes:
        sharpe_annualized: 年化夏普比率。
        sharpe_period: 单期（日频）夏普比率。
        n_observations: 样本观测数。
        n_trials: 计入多重检验的试验次数。
        expected_max_sharpe_annualized: 纯运气情形下可得的年化最大夏普期望。
        deflated_sharpe: 折减后的显著性概率（0-1）。
    """

    sharpe_annualized: float
    sharpe_period: float
    n_observations: int
    n_trials: int
    expected_max_sharpe_annualized: float
    deflated_sharpe: float


@dataclass
class BootstrapCiResult:
    """块自助法得到的年化夏普置信区间。

    Attributes:
        sharpe_annualized: 原始样本的年化夏普。
        lower: 置信区间下界。
        upper: 置信区间上界。
        confidence: 置信水平（如 0.9）。
        n_bootstrap: 自助抽样次数。
        block: 块长度（交易日）。
    """

    sharpe_annualized: float
    lower: float
    upper: float
    confidence: float
    n_bootstrap: int
    block: int


@dataclass
class NeighborhoodSummary:
    """单旋钮扰动的邻域稳定度汇总。

    Attributes:
        n_variants: 参与统计的扰动变体数量。
        delta_min: 变体相对基线的指标变化最小值；无变体时为 None。
        delta_max: 变体相对基线的指标变化最大值；无变体时为 None。
        worse_ratio: 劣于基线的变体占比（0-1）；无变体时为 None。
        reversal: 是否存在方向反转（最好变体明显优于基线且最差变体明显劣于基线）；
            无变体时为 None。
        is_plateau: 是否落在参数高原（全部变体的变化都在容差内）；
            **无变体时为 None**——"没算出东西"不能读成"通过"。
    """

    n_variants: int
    delta_min: float | None
    delta_max: float | None
    worse_ratio: float | None
    reversal: bool | None
    is_plateau: bool | None


def annualized_sharpe(
    daily_returns: list[float], trading_days: int = DEFAULT_TRADING_DAYS_PER_YEAR
) -> float:
    """计算年化夏普比率（总体标准差口径）。

    与 ``domain.research.metrics`` 的样本标准差口径略有差异：本模块用于
    统计推断（自助法/显著性），统一使用总体标准差以便与 v8 验证脚本的
    数值对照一致。

    Args:
        daily_returns: 日收益率序列（小数口径，1.0 表示 100%）。
        trading_days: 年化系数，默认 252。

    Returns:
        年化夏普比率，样本不足或零波动时返回 0。
    """
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
    std_r = math.sqrt(variance)
    if std_r <= 0:
        return 0.0
    return mean_r / std_r * math.sqrt(trading_days)


def skewness_and_kurtosis(daily_returns: list[float]) -> tuple[float, float]:
    """计算收益序列的偏度与峰度（总体矩口径，正态分布峰度为 3）。

    Args:
        daily_returns: 日收益率序列（小数口径）。

    Returns:
        (偏度, 峰度) 元组；样本不足或零波动时返回 (0.0, 3.0)。
    """
    n = len(daily_returns)
    if n < 2:
        return 0.0, 3.0
    mean_r = sum(daily_returns) / n
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / n
    std_r = math.sqrt(variance)
    if std_r <= 0:
        return 0.0, 3.0
    skew = sum((r - mean_r) ** 3 for r in daily_returns) / n / std_r**3
    kurt = sum((r - mean_r) ** 4 for r in daily_returns) / n / std_r**4
    return skew, kurt


def compute_pbo(
    performance_matrix: dict[str, list[float]], n_blocks: int | None = None
) -> PboResult:
    """用 CSCV（组合对称交叉验证）估计回测过拟合概率。

    对每个对称切分：用一半分块选样本内最优候选，再看它在另一半分块上的
    排名；把所有切分的对数几率汇总，落在他中位数以下的占比即为 PBO。

    Args:
        performance_matrix: 候选标签 → 各分块绩效（如逐窗口夏普）。所有候选
            必须拥有相同数量的分块，缺失分块的候选会被剔除。
        n_blocks: 分块数量，默认取候选矩阵的分块数；必须为偶数且 ≥ 2。

    Returns:
        PboResult。有效候选少于 3 个、或分块数低于 :data:`MIN_PBO_BLOCKS` 时
        返回 ``pbo=None`` 并给出 ``reason``（分块太少时 CSCV 的切分方式不足，
        PBO 恒为 0，直接输出 0 会被误读成"没有过拟合"）。

    Raises:
        ValueError: n_blocks 为奇数或小于 2 时抛出。
    """
    lengths = {len(values) for values in performance_matrix.values() if values}
    block_count = n_blocks if n_blocks is not None else (max(lengths) if lengths else 0)
    if block_count and (block_count < 2 or block_count % 2 != 0):
        raise ValueError("CSCV 分块数量必须为不小于 2 的偶数")
    labels = sorted(
        label
        for label, values in performance_matrix.items()
        if values and len(values) == block_count
    )
    if block_count < MIN_PBO_BLOCKS:
        return PboResult(
            pbo=None,
            n_candidates=len(labels),
            n_splits=0,
            n_blocks=block_count,
            reason=(
                f"分块数 {block_count} < {MIN_PBO_BLOCKS}，CSCV 切分方式不足，"
                "PBO 无信息量（请用更多验证窗口重跑）"
            ),
        )
    if len(labels) < 3:
        return PboResult(
            pbo=None,
            n_candidates=len(labels),
            n_splits=0,
            n_blocks=block_count,
            reason=f"有效候选只有 {len(labels)} 个（需 ≥ 3）",
        )

    n = len(labels)
    logits: list[float] = []
    for combo in itertools.combinations(range(block_count), block_count // 2):
        is_blocks = set(combo)
        oos_blocks = [i for i in range(block_count) if i not in is_blocks]
        is_scores = {
            label: _mean([performance_matrix[label][i] for i in is_blocks]) for label in labels
        }
        oos_scores = {
            label: _mean([performance_matrix[label][i] for i in oos_blocks]) for label in labels
        }
        best = max(is_scores, key=lambda label: is_scores[label])
        rank = sorted(oos_scores.values()).index(oos_scores[best]) + 1
        omega = rank / (n + 1)
        logits.append(math.log(omega / (1 - omega)))

    pbo = sum(1 for x in logits if x <= 0) / len(logits) if logits else 0.0
    return PboResult(
        pbo=pbo, n_candidates=n, n_splits=len(logits), n_blocks=block_count
    )


def deflated_sharpe_ratio(
    daily_returns: list[float],
    n_trials: int,
    trading_days: int = DEFAULT_TRADING_DAYS_PER_YEAR,
) -> DeflatedSharpeResult:
    """计算 Deflated Sharpe：计入试验次数 N 后夏普仍然显著的概率。

    使用 Bailey & López de Prado 的期望最大夏普近似，并对偏度/峰度做方差
    折减。``n_trials`` 应取"该策略研究历史上评估过的独立候选数"，
    robustness 台账与优化会话数是该值的来源。

    Args:
        daily_returns: 日收益率序列（小数口径）。
        n_trials: 计入多重检验的试验次数，必须 ≥ 1。
        trading_days: 年化系数，默认 252。

    Returns:
        DeflatedSharpeResult；样本不足时 deflated_sharpe 为 0。

    Raises:
        ValueError: n_trials < 1 时抛出。
    """
    if n_trials < 1:
        raise ValueError("试验次数必须 >= 1")
    n = len(daily_returns)
    if n < 3:
        return DeflatedSharpeResult(0.0, 0.0, n, n_trials, 0.0, 0.0)
    mean_r = sum(daily_returns) / n
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / n
    std_r = math.sqrt(variance)
    if std_r <= 0:
        return DeflatedSharpeResult(0.0, 0.0, n, n_trials, 0.0, 0.0)
    sr_period = mean_r / std_r
    skew, kurt = skewness_and_kurtosis(daily_returns)
    # 夏普估计量的渐近标准误：Var(SR) ≈ (1 + 0.5·SR²) / T
    se = math.sqrt((1 + 0.5 * sr_period**2) / max(1, n - 1))
    # 只做过一次试验时不存在多重检验膨胀（1 - 1/N 会退化到 0，无法取分位）
    expected_max = (
        se
        * (
            (1 - EULER_GAMMA) * _norm_ppf(1 - 1 / n_trials)
            + EULER_GAMMA * _norm_ppf(1 - 1 / (n_trials * math.e))
        )
        if n_trials > 1
        else 0.0
    )
    denom = math.sqrt(
        max(1e-12, 1 - skew * sr_period + (kurt - 1) / 4 * sr_period**2)
    )
    dsr = _norm_cdf((sr_period - expected_max) * math.sqrt(n - 1) / denom)
    return DeflatedSharpeResult(
        sharpe_annualized=sr_period * math.sqrt(trading_days),
        sharpe_period=sr_period,
        n_observations=n,
        n_trials=n_trials,
        expected_max_sharpe_annualized=expected_max * math.sqrt(trading_days),
        deflated_sharpe=dsr,
    )


def block_bootstrap_sharpe_ci(
    daily_returns: list[float],
    block: int = 20,
    n_bootstrap: int = 2000,
    confidence: float = 0.9,
    seed: int = 20260913,
    trading_days: int = DEFAULT_TRADING_DAYS_PER_YEAR,
) -> BootstrapCiResult:
    """用块自助法估计年化夏普的置信区间。

    连续抽样（块内保留原顺序）以保留收益序列的自相关结构，避免 iid 抽样
    低估尾部风险；块越界时循环回到序列开头续接（circular block bootstrap），
    避免"尾部块被截断"导致序列两端样本被系统性欠采样。固定随机种子保证
    同一输入得到可复现的区间。

    Args:
        daily_returns: 日收益率序列（小数口径）。
        block: 块长度（交易日），默认 20。
        n_bootstrap: 自助抽样次数，默认 2000。
        confidence: 置信水平，默认 0.9。
        seed: 随机种子，默认固定值以保证可复现。
        trading_days: 年化系数，默认 252。

    Returns:
        BootstrapCiResult；样本不足时区间上下界均为 0。

    Raises:
        ValueError: block < 1、n_bootstrap < 1 或 confidence 不在 (0, 1) 内时抛出。
    """
    if block < 1:
        raise ValueError("块长度必须 >= 1")
    if n_bootstrap < 1:
        raise ValueError("自助抽样次数必须 >= 1")
    if not 0 < confidence < 1:
        raise ValueError("置信水平必须在 (0, 1) 之间")
    n = len(daily_returns)
    if n < 3:
        return BootstrapCiResult(0.0, 0.0, 0.0, confidence, n_bootstrap, block)

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_bootstrap):
        resampled: list[float] = []
        while len(resampled) < n:
            start = rng.randrange(n)
            # 循环块：越过尾部时回到开头续接，保证每个位置被抽中的概率相同
            resampled.extend(daily_returns[(start + offset) % n] for offset in range(block))
        samples.append(annualized_sharpe(resampled[:n], trading_days))
    samples.sort()
    lower_index = int((1 - confidence) / 2 * len(samples))
    upper_index = min(len(samples) - 1, int((1 + confidence) / 2 * len(samples)))
    return BootstrapCiResult(
        sharpe_annualized=annualized_sharpe(daily_returns, trading_days),
        lower=samples[lower_index],
        upper=samples[upper_index],
        confidence=confidence,
        n_bootstrap=n_bootstrap,
        block=block,
    )


def summarize_neighborhood(
    base_value: float, variant_values: list[float], tolerance: float = 0.1
) -> NeighborhoodSummary:
    """汇总单旋钮扰动的邻域稳定度。

    参数高原的特征是"附近的参数都有效"；方向反转（既有明显更好的变体、
    又有明显更差的变体）说明结果对精确取值敏感，是需要警惕的拟合痕迹。

    Args:
        base_value: 基线的指标值（如夏普）。
        variant_values: 各扰动变体的对应指标值，需与基线同口径。
        tolerance: 判定"仍在高原内"的容差（指标绝对值），默认 0.1。

    Returns:
        NeighborhoodSummary；无变体时 n_variants 为 0。
    """
    valid = [v for v in variant_values if v is not None and math.isfinite(v)]
    if not valid:
        # 一个变体都没算出结果时不能返回 is_plateau=True：
        # 验收清单第 6 项（参数邻域无方向反转）会把"没算"读成"通过"
        return NeighborhoodSummary(0, None, None, None, None, None)
    deltas = [v - base_value for v in valid]
    worse = sum(1 for d in deltas if d < 0)
    return NeighborhoodSummary(
        n_variants=len(valid),
        delta_min=min(deltas),
        delta_max=max(deltas),
        worse_ratio=worse / len(valid),
        reversal=(max(deltas) > tolerance and min(deltas) < -tolerance),
        is_plateau=all(abs(d) <= tolerance for d in deltas),
    )


def _mean(values: list[float]) -> float:
    """计算算术平均，空列表返回 0。"""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _norm_cdf(x: float) -> float:
    """标准正态分布累积分布函数。"""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    """标准正态分布分位函数（Acklam 近似，与 v8 验证脚本同实现）。

    Args:
        p: 概率（0 < p < 1）。

    Returns:
        分位点。
    """
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
    )
