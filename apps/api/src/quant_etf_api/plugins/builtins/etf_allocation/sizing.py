"""仓位分配模块。

根据择时信号和资产排名，确定每只 ETF 的目标仓位比例。
规则：
- 择时信号决定总仓位上限
- 资产排名决定个股权重
- 单只 ETF 仓位上限 30%
- 波动率过高时自动降仓
"""

from __future__ import annotations

import logging
from typing import Any

from quant_etf_api.domain.strategies.models import AllocationPlan, AssetRanking, TimingSignal

logger = logging.getLogger(__name__)

# 择时 regime 对应的基准总仓位
_REGIME_EXPOSURE: dict[str, float] = {
    "offensive": 0.80,  # 进攻：80% 仓位
    "neutral": 0.50,    # 观望：50% 仓位
    "defensive": 0.20,  # 防守：20% 仓位
}

# 单只 ETF 仓位上限
_MAX_SINGLE_POSITION = 0.30

# 默认持有 ETF 数量
_DEFAULT_HOLD_COUNT = 5


def allocate_positions(
    timing: TimingSignal | None,
    rankings: list[AssetRanking] | None,
    params: dict[str, Any] | None = None,
) -> AllocationPlan:
    """根据择时信号和资产排名分配仓位。

    分配逻辑：
    1. 根据择时 regime 确定总仓位上限
    2. 从排名中选取前 N 只 ETF（默认 5 只）
    3. 按综合得分加权分配
    4. 单只不超过 30%，剩余为现金

    Args:
        timing: 择时信号，None 时默认中性（50% 仓位）。
        rankings: 资产排名列表（已按得分降序），None 时空仓。
        params: 策略参数，支持 max_positions（最大持仓数）。

    Returns:
        AllocationPlan，包含每只 ETF 的目标仓位和现金比例。
    """
    max_positions = params.get("max_positions", _DEFAULT_HOLD_COUNT) if params else _DEFAULT_HOLD_COUNT

    # 确定总仓位
    if timing is None:
        total_exposure = 0.50
        reasoning = "无择时信号，默认中性仓位 50%"
    else:
        total_exposure = _REGIME_EXPOSURE.get(timing.regime, 0.50)
        reasoning = f"择时信号：{timing.label}（确信度 {timing.confidence:.0f}%），目标仓位 {total_exposure:.0%}"

    # 无排名数据时全仓现金
    if not rankings:
        return AllocationPlan(
            positions={},
            total_exposure=0.0,
            cash_ratio=1.0,
            reasoning=reasoning + "；无可选资产，全部持有现金",
        )

    # 选取排名前 N 的 ETF
    top_n = rankings[:max_positions]

    # 过滤掉得分为 0 或负数的
    eligible = [r for r in top_n if r.score > 0]
    if not eligible:
        return AllocationPlan(
            positions={},
            total_exposure=0.0,
            cash_ratio=1.0,
            reasoning=reasoning + "；所有候选资产得分为 0，全部持有现金",
        )

    # 按得分加权分配
    total_score = sum(r.score for r in eligible)
    raw_positions: dict[str, float] = {}
    for r in eligible:
        weight = r.score / total_score
        raw_positions[r.etf_code] = weight * total_exposure

    # 单只仓位上限裁剪
    positions: dict[str, float] = {}
    for code, weight in raw_positions.items():
        positions[code] = min(weight, _MAX_SINGLE_POSITION)

    # 裁剪后总仓位可能低于目标，不再重新分配（保守策略）
    actual_exposure = round(sum(positions.values()), 4)
    cash_ratio = round(1.0 - actual_exposure, 4)

    # 补充分配理由
    held_codes = list(positions.keys())
    held_names = [r.name_cn for r in eligible if r.etf_code in held_codes]
    reasoning += f"；持有 {len(held_codes)} 只：{'、'.join(held_names)}"

    # 格式化仓位比例
    positions = {k: round(v, 4) for k, v in positions.items()}

    return AllocationPlan(
        positions=positions,
        total_exposure=round(actual_exposure, 4),
        cash_ratio=cash_ratio,
        reasoning=reasoning,
    )
