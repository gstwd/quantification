"""仓位等比缩放与双腿持仓合成（纯领域逻辑）。

风险腿的语义是"只动仓位、不动成分"：保持现有成分与相对权重不变，把组合总
仓位缩放到择时给出的目标暴露。本模块提供该操作的唯一实现，供回测主循环与
实时摘要共用，避免两侧各写一份产生口径分叉。

舍入口径与 ``engine/risk.py`` 的历史实现一致（逐资产 4 位小数），但额外做
"总量不超目标"的和差校正：逐腿四舍五入会带来 n×5e-5 量级的误差，若不校正，
反复缩放会让实际暴露缓慢漂移并可能越过 ``max_portfolio_exposure`` /
``min_cash_ratio`` 约束。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 和差校正容差：小于该值的偏差视为四舍五入噪声，不再从某个资产上扣减
_EXPOSURE_EPSILON = 1e-9


def scale_to_exposure(
    positions: dict[str, float],
    target_exposure: float,
) -> dict[str, float]:
    """保持成分与相对权重，把总仓位等比缩放到目标暴露。

    Args:
        positions: 现有持仓权重，key=index_code，value=权重（非正权重被丢弃）。
        target_exposure: 目标总仓位（0-1）。<=0 表示清仓。

    Returns:
        缩放后的持仓权重（4 位小数，非正权重已剔除）。

    Notes:
        目标暴露为 0 时返回空字典（清仓）；空持仓不建仓（风险腿不选股）。
        逐腿四舍五入后若总量超过目标，从最大的一腿扣回差额，保证实际暴露
        **不超过**目标仓位。
    """
    # 1. 剔除非正权重：占比为零的资产不应占据仓位键位
    held = {code: weight for code, weight in positions.items() if weight > 0}
    if not held:
        return {}
    if target_exposure <= 0:
        return {}

    current_exposure = sum(held.values())
    factor = target_exposure / current_exposure
    scaled = {code: round(weight * factor, 4) for code, weight in held.items()}

    # 2. 和差校正：逐腿取整可能让总量越过目标仓位（风控上限），从最大腿扣回。
    #    反复缩放时舍入噪声不能累积，因此逐次扣减直到回到目标以内（通常 1 次）。
    for _ in range(len(scaled)):
        excess = round(sum(scaled.values()) - target_exposure, 10)
        if excess <= _EXPOSURE_EPSILON:
            break
        largest = max(scaled, key=lambda code: scaled[code])
        scaled[largest] = round(max(0.0, scaled[largest] - excess), 4)

    # 3. 校正后可能产生 0 权重，顺带清理，避免落库出现 0 持仓
    return {code: weight for code, weight in scaled.items() if weight > 0}


def compose_two_leg_positions(
    *,
    previous: dict[str, float],
    selection_target: dict[str, float],
    timing_target_exposure: float,
    should_select: bool,
    should_scale_risk: bool,
) -> dict[str, float]:
    """按"选股腿换成分、风险腿缩仓位"合成当日目标持仓。

    合成口径（回测与实时共用）：

    1. **两腿同日到期**（含两腿同频的旧配置形态）：直接采用引擎当日给出的
       "新成分 + 择时目标仓位"。这与改造前的单腿行为完全一致——旧口径也是
       "择时目标仓位控制总仓位、引擎输出的成分作为持仓"。
    2. **仅选股腿到期**：换成新成分，并把总量缩放到调仓前的实际总仓位
       （只换成分、不调整暴露）。
    3. **仅风险腿到期**：保持现有成分与相对权重，等比缩放到择时目标总仓位；
       现有仓位为空时不建仓（风险腿不负责选股）。
    4. **两腿都不到期**：维持现有持仓。

    Args:
        previous: 调仓前的实际持仓权重。
        selection_target: 引擎当日给出的目标持仓权重（已含择时目标仓位）。
        timing_target_exposure: 引擎当日给出的目标总仓位。
        should_select: 选股腿当日是否到期。
        should_scale_risk: 风险腿当日是否到期。

    Returns:
        合成后的当日目标持仓权重。
    """
    if should_select:
        if not selection_target:
            # 引擎当日无入选资产：与改造前一致，整仓清空（不存在"缩放到旧仓位"）
            return {}
        if should_scale_risk:
            # 两腿同日：引擎权重即"新成分 + 择时目标仓位"，与改造前完全等价
            return dict(selection_target)
        previous_exposure = sum(previous.values())
        if previous_exposure <= 0:
            # 首次建仓：没有"当前总仓位"可维持，直接使用引擎的择时目标仓位
            logger.debug("选股腿首次建仓，直接采用引擎目标仓位")
            return dict(selection_target)
        # 只换成分、不调整暴露：把新成分缩放到调仓前的实际总仓位
        return scale_to_exposure(selection_target, previous_exposure)
    if should_scale_risk:
        # 风险腿只动仓位：现有成分为空时不建仓
        return scale_to_exposure(previous, timing_target_exposure)
    return dict(previous)
