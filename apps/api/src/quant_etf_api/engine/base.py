"""策略引擎核心数据结构：EngineContext 和 EngineResult。

EngineContext 替代旧的 StrategyContextData，使用结构化字段取代无类型 dict。
EngineResult 统一输出信号模式和配置模式的结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from quant_etf_api.domain.strategies.models import (
    AssetRanking,
    StrategyResult,
    TimingSignal,
)
from quant_etf_api.engine.pipeline_detail import PipelineDetail


@dataclass
class EngineContext:
    """策略引擎执行上下文。

    Attributes:
        trade_date: 交易日。
        universe: 资产宇宙列表，每项含 etf_code、name_cn、category。
        asset_factors: 每资产的因子值，key=(etf_code, factor_id)。
        market_factors: 市场级因子值（用于择时），key=factor_id。
        asset_metadata: 资产元数据，key=etf_code。
        index_valuation: 指数估值映射，key=index_code，value=含 pe/pb 百分位等字段的字典。
            类型化替代 extra["index_valuation"]，避免无类型逃生舱。
        extra: 扩展字段（原始 K 线数据等）。
    """

    trade_date: date
    universe: list[dict[str, Any]]
    asset_factors: dict[tuple[str, str], float | None] = field(default_factory=dict)
    market_factors: dict[str, float | None] = field(default_factory=dict)
    asset_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    index_valuation: dict[str, dict[str, float | None]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    # 持仓状态（回测/实时模式下由调用方填充，用于止损止盈和再平衡）
    current_positions: dict[str, float] = field(default_factory=dict)
    entry_prices: dict[str, float] = field(default_factory=dict)
    holding_days: dict[str, int] = field(default_factory=dict)


@dataclass
class EngineResult:
    """策略引擎执行结果。

    Attributes:
        trade_date: 交易日。
        strategy_id: 策略标识。
        timing: 择时信号，无择时配置时为 None。
        scores: 每资产综合得分，key=etf_code。
        rankings: 资产排名列表（已排序）。
        positions: 目标仓位权重，key=etf_code。信号模式下为空 dict。
        total_exposure: 总仓位比例。
        cash_ratio: 现金比例。
        strategy_results: 兼容旧接口的 StrategyResult 列表。
        pipeline_detail: 管线调试详情，含评分分解、过滤明细等中间数据。
    """

    trade_date: date
    strategy_id: str
    timing: TimingSignal | None
    scores: dict[str, float]
    rankings: list[AssetRanking]
    positions: dict[str, float]
    total_exposure: float
    cash_ratio: float
    strategy_results: list[StrategyResult]
    pipeline_detail: PipelineDetail | None = None
