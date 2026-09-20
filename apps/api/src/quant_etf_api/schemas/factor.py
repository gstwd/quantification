"""因子层 API 响应 Schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FactorParameterSpecResponse(BaseModel):
    """模板单个可覆盖参数的声明。

    Attributes:
        type: 参数类型，integer 或 number。
        minimum: 允许的最小值（含），None 表示不设下界。
        maximum: 允许的最大值（含），None 表示不设上界。
        default: 默认值。
        description: 参数中文说明。
    """

    type: Literal["integer", "number"]
    minimum: float | None = None
    maximum: float | None = None
    default: float
    description: str


class FactorSpecResponse(BaseModel):
    """因子模板元数据响应，对应 GET /factors/ 端点。

    Attributes:
        factor_id: 因子模板 ID（factor_definition.factor_id），如 sma。
        name: 模板中文名称，如 简单移动平均线。
        category: 因子类别：volume/momentum/volatility/flow/valuation。
        version: 模板语义化版本号。
        description: 计算逻辑说明。
        required_data: 依赖的数据源列表。
        is_active: 模板是否启用。
        value_shape: 因子值形态：asset=每资产值，market=市场级值。
        usage: 适用位置数组：timing/score/filter/rank。
        parameter_schema: 可覆盖参数声明，空对象表示零参数模板。
        default_params: 模板固有参数口径（零参数模板记录其固定口径）。
    """

    factor_id: str
    name: str
    category: str | None
    version: str
    description: str
    required_data: list[str]
    is_active: bool
    value_shape: Literal["asset", "market"] = "asset"
    usage: list[str] = Field(default_factory=list)
    parameter_schema: dict[str, FactorParameterSpecResponse] = Field(default_factory=dict)
    default_params: dict | None = None


class FactorUpdateRequest(BaseModel):
    """因子编辑请求体，对应 PATCH /factors/{factor_id} 端点。

    所有字段均为可选，仅传入需要修改的字段。

    Attributes:
        name: 因子中文名称。
        description: 计算逻辑说明。
        category: 因子类别。
        is_active: 是否启用。
    """

    name: str | None = None
    description: str | None = None
    category: str | None = None
    is_active: bool | None = None


class CrossSectionRow(BaseModel):
    """横截面数据行，包含指数中文名。

    Attributes:
        index_code: 指数代码。
        name_cn: 指数中文名称。
        factor_value_numeric: 因子数值。
        factor_value_text: 因子文本值。
    """

    index_code: str
    name_cn: str
    factor_value_numeric: float | None = None
    factor_value_text: str | None = None


class CrossSectionResponse(BaseModel):
    """横截面查询响应。

    Attributes:
        factor_id: 因子模板标识。
        params: 本次计算的规范化参数。
        trade_date: 数据日期。
        rows: 横截面数据行列表。
    """

    factor_id: str
    params: dict = Field(default_factory=dict)
    trade_date: str
    rows: list[CrossSectionRow]


class ICPoint(BaseModel):
    """IC 时间序列单点。

    Attributes:
        trade_date: 交易日。
        ic: Rank IC 值。
        cross_section_n: 该日参与计算的横截面指数数量。
    """

    trade_date: str
    ic: float
    cross_section_n: int = 0


class ICSummary(BaseModel):
    """IC 汇总统计。

    Attributes:
        ic_mean: IC 均值。
        ic_std: IC 标准差。
        ic_ir: IC_IR（IC 均值 / IC 标准差，未年化）。
        t_stat: t 统计量（ic_ir × sqrt(effective_n)），|t| > 2 才算显著。
        ic_positive_ratio: IC>0 的比例。
        count: 有效 IC 数据点数量。
        effective_n: 折算重叠窗口后的有效样本数（count // forward_days）。
        overlap: 前瞻窗口是否重叠（forward_days > 1 时为 True，ic_std 会低估）。
        cross_section_n_avg: 有效观测的平均横截面指数数量。
        cross_section_n_min: 有效观测的最小横截面指数数量。
        cross_section_n_max: 有效观测的最大横截面指数数量。
        cross_section_n_required: 有效 IC 所需的最小横截面指数数量。
        excluded_low_n_days: 因横截面不足被排除的交易日数量。
        dropped_no_forward_days: 因缺少前瞻行情被丢弃的交易日数量。
        insufficient_reason: 未产出有效 IC 时的中文原因说明。
    """

    ic_mean: float | None = None
    ic_std: float | None = None
    ic_ir: float | None = None
    t_stat: float | None = None
    ic_positive_ratio: float | None = None
    count: int = 0
    effective_n: int = 0
    overlap: bool = False
    cross_section_n_avg: float | None = None
    cross_section_n_min: int | None = None
    cross_section_n_max: int | None = None
    cross_section_n_required: int = 0
    excluded_low_n_days: int = 0
    dropped_no_forward_days: int = 0
    insufficient_reason: str | None = None


class ICResponse(BaseModel):
    """因子 IC 分析响应。

    Attributes:
        factor_id: 因子模板标识。
        params: 本次计算的规范化参数。
        summary: IC 汇总统计。
        series: IC 时间序列。
    """

    factor_id: str
    params: dict = Field(default_factory=dict)
    summary: ICSummary
    series: list[ICPoint]


class CorrelationResponse(BaseModel):
    """因子相关性矩阵响应。

    Attributes:
        factor_ids: 因子标识列表（矩阵行列顺序）。
        matrix: 相关系数二维矩阵；样本不足或相关未定义（常数输入）的格子为
            None，不再伪造为 0；对角线恒为 1.0。
        pair_counts: 与 matrix 同形的成对样本数矩阵，标识每个格子实际用了
            多少指数计算。
        index_count: 当日有任一因子值的指数数量（原始横截面规模）。
        undetermined_pair_count: 未能定值的因子对数量（成对样本不足或常数输入）。
        trade_date: 数据日期。
    """

    factor_ids: list[str]
    matrix: list[list[float | None]]
    pair_counts: list[list[int]] = Field(default_factory=list)
    index_count: int
    undetermined_pair_count: int = 0
    trade_date: str
