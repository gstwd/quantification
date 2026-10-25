"""组合风险模型（纯领域逻辑）：逐日收益面板、协方差、风险平价权重与事前波动率。

风险平价分配与波动率目标共用同一份收益面板，保证两者的口径一致：

- 收益取原始行情的收盘到收盘涨跌（不用前向填充行情：那是收益结算专用，缺口日会
  伪造 0 收益并压低波动）；
- 协方差只用窗口内所有相关资产都有收益的交易日；有效天数不足时返回 None，由调用方
  按约定退回（风险平价退回等权、波动率目标不缩放）。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from quant_etf_api.domain.common.numeric import price_invalid

# 年化系数（交易日）
TRADING_DAYS_PER_YEAR = 252
# 风险平价迭代参数：收敛阈值与最大迭代次数
_RP_TOLERANCE = 1e-10
_RP_MAX_ITER = 500


@dataclass
class ReturnPanel:
    """按交易日 × 资产排列的逐日收益面板（小数口径，缺失为 NaN）。

    Attributes:
        dates: 升序交易日轴。
        codes: 资产代码。
        returns: 形如 (len(dates), len(codes)) 的收益矩阵；第 t 行为 [收盘 t-1, 收盘 t] 的涨跌。
    """

    dates: list[date]
    codes: list[str]
    returns: np.ndarray
    _date_index: dict[date, int] = field(default_factory=dict, repr=False)
    _code_index: dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """建立日期与代码的下标索引。"""
        self._date_index = {day: i for i, day in enumerate(self.dates)}
        self._code_index = {code: j for j, code in enumerate(self.codes)}

    @classmethod
    def from_bars(
        cls, bars: Mapping[tuple[str, date], Any], codes: Sequence[str]
    ) -> "ReturnPanel":
        """由原始日线构建收益面板（交易日轴取这些资产出现过的全部日期）。

        Args:
            bars: 原始行情，key=(index_code, trade_date)，对象需有 close_price。
            codes: 资产代码。

        Returns:
            收益面板；相邻两个交易日任一侧收盘价无效时该格为 NaN。
        """
        wanted = list(dict.fromkeys(codes))
        code_set = set(wanted)
        days = sorted({day for (code, day) in bars if code in code_set})
        closes = np.full((len(days), len(wanted)), np.nan)
        day_index = {day: i for i, day in enumerate(days)}
        code_index = {code: j for j, code in enumerate(wanted)}
        for (code, day), bar in bars.items():
            if code not in code_set:
                continue
            close = getattr(bar, "close_price", None)
            if price_invalid(close):
                continue
            closes[day_index[day], code_index[code]] = float(close)
        returns = np.full_like(closes, np.nan)
        if len(days) > 1:
            with np.errstate(divide="ignore", invalid="ignore"):
                returns[1:] = closes[1:] / closes[:-1] - 1
        returns[~np.isfinite(returns)] = np.nan
        return cls(dates=days, codes=wanted, returns=returns)

    def window(self, end: date, length: int, codes: Sequence[str]) -> np.ndarray | None:
        """取截至 end（含）最近 length 个交易日、指定资产的收益子矩阵。

        Args:
            end: 窗口末日（决策日）；不在面板日期轴上时取不晚于它的最近交易日。
            length: 窗口长度（交易日）。
            codes: 资产代码（面板中不存在的资产整列为 NaN）。

        Returns:
            形如 (≤length, len(codes)) 的矩阵；end 早于面板首日时为 None。
        """
        end_idx = self._date_index.get(end)
        if end_idx is None:
            end_idx = bisect_right(self.dates, end) - 1
            if end_idx < 0:
                return None
        start = max(0, end_idx - length + 1)
        block = np.full((end_idx - start + 1, len(codes)), np.nan)
        for j, code in enumerate(codes):
            col = self._code_index.get(code)
            if col is not None:
                block[:, j] = self.returns[start : end_idx + 1, col]
        return block


def covariance(block: np.ndarray | None, min_rows: int) -> np.ndarray | None:
    """用所有资产都有收益的交易日估计日收益协方差（样本口径）。

    Args:
        block: 收益子矩阵（行=交易日，列=资产）。
        min_rows: 最少有效交易日数。

    Returns:
        协方差矩阵；有效天数不足或无资产时为 None。
    """
    if block is None or block.size == 0 or block.shape[1] == 0:
        return None
    valid = block[np.all(np.isfinite(block), axis=1)]
    if valid.shape[0] < max(2, min_rows):
        return None
    return np.atleast_2d(np.cov(valid, rowvar=False))


def risk_parity_weights(cov: np.ndarray) -> np.ndarray | None:
    """求解多头等风险贡献权重（和为 1）。

    用循环坐标下降求解 ``min ½wᵀΣw − (1/n)Σ log w``，最优解按和归一化后即等风险贡献
    （Griveau-Billion 等的 CCD 算法），对正定协方差保证收敛且权重为正。

    Args:
        cov: 协方差矩阵。

    Returns:
        权重向量；协方差含非正方差或数值不收敛时为 None。
    """
    n = cov.shape[0]
    diag = np.diag(cov)
    if n == 0 or not np.all(np.isfinite(cov)) or np.any(diag <= 0):
        return None
    if n == 1:
        return np.ones(1)
    budget = 1.0 / n
    w = 1.0 / np.sqrt(diag)
    w = w / w.sum()
    for _ in range(_RP_MAX_ITER):
        previous = w.copy()
        for i in range(n):
            # Σ_ii w_i² + (Σ_{j≠i} Σ_ij w_j) w_i − b = 0 的正根
            cross = float(cov[i] @ w - cov[i, i] * w[i])
            w[i] = (-cross + math.sqrt(cross * cross + 4 * cov[i, i] * budget)) / (2 * cov[i, i])
        if np.max(np.abs(w - previous)) < _RP_TOLERANCE:
            break
    total = w.sum()
    if not np.isfinite(total) or total <= 0:
        return None
    return w / total


def annualized_volatility(weights: Mapping[str, float], cov: np.ndarray, codes: Sequence[str]) -> float:
    """按协方差估算组合年化波动率。

    Args:
        weights: 组合权重（现金隐含为 1 − Σw）。
        cov: 与 codes 顺序一致的日收益协方差。
        codes: 资产代码。

    Returns:
        年化波动率（小数）。
    """
    vector = np.array([weights.get(code, 0.0) for code in codes])
    variance = float(vector @ cov @ vector)
    return math.sqrt(max(variance, 0.0) * TRADING_DAYS_PER_YEAR)
