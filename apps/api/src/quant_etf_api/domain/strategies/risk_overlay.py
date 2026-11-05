"""路径依赖风控叠加层（纯领域逻辑）：回撤止损、移动止损、波动率目标与偏离调仓。

这些规则都依赖"策略走到今天的路径"（净值峰值、持仓入场以来的高点、上一次目标），
不能只看当日截面，因此不放在逐日无状态的引擎管线里，而是在回测主循环合成当日目标
之后施加；实时决策通过回放同一主循环重建状态，与回测共用本模块。

时序（与回测"T 日决策 → T+1 行情"一致）：

1. 决策日 T：:func:`apply_risk_overlay` 读取截至 T 收盘的状态，修改当日目标；
2. 结算 [T, T+1] 收益后：:func:`advance_overlay_state` 用该窗口的组合收益与持仓资产
   收盘价推进状态，新触发的止损在下一决策日强制成交（不等日历调仓日）。

冷却期以决策日计：在 T 日收盘触发的 N 日冷却，覆盖 T+1 起的 N 个决策日。
回撤止损到期后以当时净值重置峰值（否则会立刻再次触发），仓位在下一次日历调仓时恢复。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from quant_etf_api.domain.portfolio.risk_model import annualized_volatility
from quant_etf_api.domain.portfolio.scaling import scale_to_exposure
from quant_etf_api.domain.portfolio.turnover import compute_turnover

# 领域层不依赖引擎配置模型：风控 / 偏离调仓配置按属性鸭子类型读取
# （RiskConfig 的 vol_target / drawdown_stop / trailing_stop / has_overlay，
#  RebalanceBandConfig 的 min_turnover / max_drift）
RiskConfig = Any
RebalanceBandConfig = Any

# 协方差查询：给定资产代码列表，返回与之同序的日收益协方差（历史不足时为 None）
CovarianceLookup = Callable[[Sequence[str]], "np.ndarray | None"]


@dataclass(frozen=True)
class RiskOverlayState:
    """叠加层在某个收盘时点的状态。

    Attributes:
        nav: 策略净值（起点 1.0）。
        peak_nav: 净值峰值（回撤止损到期后重置）。
        stop_days_left: 回撤止损剩余冷却决策日，0 表示未处于止损。
        asset_peaks: 持仓资产自入场以来的最高收盘价。
        asset_cooldowns: 被移动止损清出的资产 → 剩余冷却决策日。
        force_rebalance: 上一窗口新触发了止损，下一决策日必须成交。
    """

    nav: float = 1.0
    peak_nav: float = 1.0
    stop_days_left: int = 0
    asset_peaks: Mapping[str, float] = field(default_factory=dict)
    asset_cooldowns: Mapping[str, int] = field(default_factory=dict)
    force_rebalance: bool = False


@dataclass(frozen=True)
class OverlayResult:
    """叠加层对当日目标的修改结果。

    Attributes:
        positions: 修改后的目标持仓。
        force: 当日是否必须成交（新触发止损）。
        removed: 因移动止损冷却被清出的资产。
        exposure_scale: 回撤止损与波动率目标合计的总仓位缩放比例（1 表示未缩放）。
    """

    positions: dict[str, float]
    force: bool = False
    removed: tuple[str, ...] = ()
    exposure_scale: float = 1.0


def apply_risk_overlay(
    target: Mapping[str, float],
    state: RiskOverlayState,
    risk: RiskConfig,
    covariance_of: CovarianceLookup | None = None,
) -> OverlayResult:
    """在当日目标上施加叠加层：移出冷却资产 → 回撤止损封顶 → 波动率目标缩放。

    三者都只减少仓位（清出的权重留作现金，不再分配），不会突破既有风控上限。

    Args:
        target: 合成后的当日目标持仓。
        state: 截至决策日收盘的叠加层状态。
        risk: 风控配置。
        covariance_of: 协方差查询（波动率目标需要）；None 时跳过波动率目标。

    Returns:
        OverlayResult。
    """
    positions = {code: weight for code, weight in target.items() if weight > 0}
    removed = tuple(sorted(c for c in positions if state.asset_cooldowns.get(c, 0) > 0))
    for code in removed:
        del positions[code]
    exposure = sum(positions.values())
    scale = 1.0
    if risk.drawdown_stop is not None and state.stop_days_left > 0 and exposure > 0:
        cap = risk.drawdown_stop.exposure
        if exposure > cap:
            scale *= cap / exposure
    vol_target = risk.vol_target
    if vol_target is not None and covariance_of is not None and positions:
        codes = sorted(positions)
        cov = covariance_of(codes)
        if cov is not None:
            scaled_weights = {code: positions[code] * scale for code in codes}
            vol = annualized_volatility(scaled_weights, cov, codes)
            if vol > vol_target.target:
                scale *= max(vol_target.target / vol, vol_target.floor)
    if scale < 1.0 and exposure > 0:
        positions = scale_to_exposure(positions, exposure * scale)
    return OverlayResult(
        positions=positions,
        force=state.force_rebalance,
        removed=removed,
        exposure_scale=round(scale, 6),
    )


def advance_overlay_state(
    state: RiskOverlayState,
    risk: RiskConfig,
    *,
    portfolio_return_pct: float,
    held: Mapping[str, float],
    start_closes: Mapping[str, float | None],
    end_closes: Mapping[str, float | None],
) -> RiskOverlayState:
    """用一个收益窗口 [T, T+1] 推进叠加层状态。

    Args:
        state: 窗口起点（T 收盘）的状态。
        risk: 风控配置。
        portfolio_return_pct: 窗口组合收益率（%）。
        held: 窗口内实际承担收益的持仓。
        start_closes: 持仓资产在 T 的收盘价（首次持有时作为入场价）。
        end_closes: 持仓资产在 T+1 的收盘价；缺失时该资产高点不更新、不触发。

    Returns:
        窗口终点（T+1 收盘）的状态。
    """
    force = False
    cooldowns = {code: days - 1 for code, days in state.asset_cooldowns.items() if days > 1}
    nav = state.nav * (1 + portfolio_return_pct / 100)
    peak = max(state.peak_nav, nav)
    stop_left = state.stop_days_left
    stop = risk.drawdown_stop
    if stop is not None:
        if stop_left > 0:
            stop_left -= 1
            if stop_left == 0:
                # 冷却结束：以当前净值作为新峰值，避免一解除就因同一段回撤再次触发
                peak = nav
        elif peak > 0 and nav / peak - 1 <= -stop.threshold:
            stop_left = stop.cooldown_days
            force = True
    peaks: dict[str, float] = {}
    trailing = risk.trailing_stop
    if trailing is not None:
        for code, weight in sorted(held.items()):
            if weight <= 0:
                continue
            base = state.asset_peaks.get(code) or start_closes.get(code)
            end = end_closes.get(code)
            if base is None or base <= 0:
                continue
            if end is None or end <= 0:
                peaks[code] = base
                continue
            high = max(base, end)
            if end / high - 1 <= -trailing.threshold:
                cooldowns[code] = trailing.cooldown_days
                force = True
            else:
                peaks[code] = high
    return RiskOverlayState(
        nav=nav,
        peak_nav=peak,
        stop_days_left=stop_left,
        asset_peaks=peaks,
        asset_cooldowns=cooldowns,
        force_rebalance=force,
    )


def max_weight_drift(actual: Mapping[str, float], target: Mapping[str, float]) -> float:
    """实际持仓相对目标的最大单资产权重偏离。

    Args:
        actual: 实际持仓（已漂移）。
        target: 最近一次目标。

    Returns:
        max_k |actual_k − target_k|。
    """
    codes = set(actual) | set(target)
    if not codes:
        return 0.0
    return max(abs(actual.get(code, 0.0) - target.get(code, 0.0)) for code in codes)


@dataclass(frozen=True)
class DayTarget:
    """当日最终目标与是否成交。

    Attributes:
        positions: 当日目标持仓（仅 should_rebalance 时生效）。
        should_rebalance: 当日是否成交。
        reason: 偏离调仓 / 止损带来的额外说明：band_drift / band_skip / risk_stop。
        overlay: 叠加层结果（未启用时为 None）。
    """

    positions: dict[str, float]
    should_rebalance: bool
    reason: str | None = None
    overlay: OverlayResult | None = None


def resolve_day_target(
    composed: Mapping[str, float],
    *,
    legs_due: bool,
    base: Mapping[str, float],
    last_target: Mapping[str, float] | None,
    risk: RiskConfig | None,
    band: RebalanceBandConfig | None,
    overlay_state: RiskOverlayState | None,
    covariance_of: CovarianceLookup | None = None,
) -> DayTarget:
    """合成当日最终目标：偏离触发再平衡 → 风控叠加层 → 小额换手跳过。

    未启用偏离调仓与叠加层时原样返回（``positions=composed``、成交与否只看日历腿）。

    Args:
        composed: 两腿合成后的当日目标。
        legs_due: 当日是否有日历调仓腿到期。
        base: 调仓前实际持仓（``BacktestExecutor.composition_base``）。
        last_target: 最近一次实际成交的目标；None 表示尚未成交过。
        risk: 风控配置。
        band: 偏离调仓配置。
        overlay_state: 叠加层状态（启用叠加层时必须提供）。
        covariance_of: 协方差查询（波动率目标需要）。

    Returns:
        DayTarget。
    """
    positions = dict(composed)
    should = legs_due
    reason: str | None = None
    if band is not None and not should and band.max_drift is not None and last_target:
        if max_weight_drift(base, last_target) > band.max_drift:
            positions = dict(last_target)
            should = True
            reason = "band_drift"
    overlay: OverlayResult | None = None
    if risk is not None and risk.has_overlay and overlay_state is not None:
        overlay = apply_risk_overlay(positions, overlay_state, risk, covariance_of)
        positions = overlay.positions
        if overlay.force and not should:
            should = True
            reason = "risk_stop"
    if (
        band is not None
        and band.min_turnover is not None
        and should
        and reason is None
        and any(weight > 0 for weight in base.values())
        and compute_turnover(dict(base), positions) < band.min_turnover
    ):
        should = False
        reason = "band_skip"
    return DayTarget(positions=positions, should_rebalance=should, reason=reason, overlay=overlay)
