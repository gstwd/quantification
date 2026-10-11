"""策略引擎核心数据结构：EngineContext 和 EngineResult。

EngineContext 替代旧的 StrategyContextData，使用结构化字段取代无类型 dict。
EngineResult 统一输出策略评分、排名和目标仓位结果。
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
        universe: 资产宇宙列表，每项含 index_code、name_cn、category。
        asset_factors: 每资产的因子值，key=(index_code, 因子引用名)。
            值为 None 表示该因子在该资产上取不到数值。
        market_factors: 市场级因子值（用于择时），key=因子引用名。
        asset_metadata: 资产元数据，key=index_code。
        factor_failures: 现算过程抛出异常的因子引用名集合；
            用于把"计算失败"与"数据不足"区分开。
        market_shape_refs: 解析为市场级模板的因子引用；引擎按资产检查
            "缺失即剔除"时豁免这些引用（它们没有逐资产取值）。
        display_only_refs: 仅用于结果展示（排名子因子）的引用；缺失时不告警。
    """

    trade_date: date
    universe: list[dict[str, Any]]
    asset_factors: dict[tuple[str, str], float | None] = field(default_factory=dict)
    market_factors: dict[str, float | None] = field(default_factory=dict)
    asset_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    factor_failures: set[str] = field(default_factory=set)
    market_shape_refs: set[str] = field(default_factory=set)
    display_only_refs: set[str] = field(default_factory=set)


@dataclass
class EngineResult:
    """策略引擎执行结果。

    Attributes:
        trade_date: 交易日。
        strategy_id: 策略标识。
        timing: 择时信号，无择时配置时为 None。
        scores: 每资产综合得分，key=index_code。
        rankings: 资产排名列表（已排序）。
        positions: 目标仓位权重，key=index_code；无入选资产时为空 dict。
        total_exposure: 总仓位比例。
        cash_ratio: 现金比例。
        strategy_results: 兼容旧接口的 StrategyResult 列表。
        pipeline_detail: 管线调试详情，含评分分解、过滤明细等中间数据。
        timing_target_exposure: 择时 regime 决定的目标总仓位（已过风控上限），
            与当日选股结果无关；风险腿单独到期时据此缩放现有持仓。
        max_asset_weight: 生效风控的单资产仓位上限，未配置风控时为 None；
            双腿单独到期时缩放后的持仓据此裁剪。
        effective_scoring_mode: regime 覆盖后实际生效的评分模式。
        excluded: 因决策所需因子缺失而在评分前被剔除的资产，key=index_code，
            value=原因（MISSING_FACTOR）。
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
    timing_target_exposure: float = 0.0
    max_asset_weight: float | None = None
    effective_scoring_mode: str = "absolute"
    excluded: dict[str, str] = field(default_factory=dict)
