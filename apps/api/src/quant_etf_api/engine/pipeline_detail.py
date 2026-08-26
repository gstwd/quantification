"""管线调试详情数据结构。

提供评分分解、过滤明细、择时明细等中间数据，
用于前端展示策略决策的完整推理过程，方便排查"为什么某个资产被选中/未被选中"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FactorScoreBreakdown:
    """单个因子在评分中的贡献分解。

    Attributes:
        factor_id: 因子唯一标识。
        raw_value: 因子原始值，None 表示数据缺失。
        transformed_value: 经变换函数处理后的值。
        weight: 因子在评分配置中的权重。
        contribution: 该因子对资产综合得分的贡献值（= transformed * weight / total_abs_weight）。
        status: 因子状态，ok/missing_excluded/missing_zero/missing_ignored。
    """

    factor_id: str
    raw_value: float | None
    transformed_value: float | None
    weight: float
    contribution: float = 0.0
    status: str = "ok"


@dataclass
class AssetScoreDetail:
    """单个资产的评分明细。

    Attributes:
        etf_code: 资产代码（指数代码）。
        name_cn: 资产中文名称。
        raw_score: 横截面变换前的原始加权得分，None 表示被排除。
        final_score: 横截面变换后的最终得分（0-100），None 表示被排除。
        factors: 各因子的贡献分解列表。
        excluded: 是否因因子缺失被排除。
        exclude_reason: 排除原因说明。
    """

    etf_code: str
    name_cn: str
    raw_score: float | None = None
    final_score: float | None = None
    factors: list[FactorScoreBreakdown] = field(default_factory=list)
    excluded: bool = False
    exclude_reason: str = ""


@dataclass
class FilterRuleResult:
    """单条过滤规则对单个资产的判定结果。

    Attributes:
        rule_index: 规则在配置中的序号（从 0 开始）。
        factor: 被检查的因子 ID。
        op: 操作符（gt/lt/gte/lte/eq/neq/between）。
        threshold: 阈值，固定阈值时为 float/list，跨因子比较时为 compare_to 因子 ID 字符串。
        factor_value: 因子实际值，None 表示无数据。
        compare_value: 跨因子比较时的参照因子值，非跨因子比较时为 None。
        passed: 是否通过该条规则。
        missing: 因子值（或 compare_to 参照值）是否缺失。
        missing_strategy: 缺失时的处理策略（pass/fail/exclude）。
    """

    rule_index: int
    factor: str
    op: str
    threshold: Any = None
    factor_value: float | None = None
    compare_value: float | None = None
    passed: bool = False
    missing: bool = False
    missing_strategy: str = "fail"


@dataclass
class AssetFilterDetail:
    """单个资产的过滤明细。

    Attributes:
        etf_code: 资产代码。
        name_cn: 资产中文名称。
        passed: 是否通过全部过滤规则。
        rule_results: 各条规则的判定结果列表。
        fail_reason: 失败原因描述（AND 逻辑时为第一条未通过规则，OR 逻辑时为全部未通过）。
    """

    etf_code: str
    name_cn: str
    passed: bool = True
    rule_results: list[FilterRuleResult] = field(default_factory=list)
    fail_reason: str = ""


@dataclass
class PipelineDetail:
    """管线调试完整详情。

    从评分、过滤、择时三个维度记录中间数据，
    前端可通过展开面板逐资产逐因子的查看决策推理过程。

    Attributes:
        scoring: 所有资产的评分明细列表（含被排除的资产）。
        filter_results: 过滤明细列表，无过滤配置时为 None。
        timing_detail: 择时信号明细，无择时配置时为 None。
        cross_section_stats: 横截面统计信息（均值、标准差等），
            仅 zscore/rank 模式有值。
    """

    scoring: list[AssetScoreDetail] = field(default_factory=list)
    filter_results: list[AssetFilterDetail] | None = None
    timing_detail: dict[str, Any] | None = None
    cross_section_stats: dict[str, Any] | None = None
