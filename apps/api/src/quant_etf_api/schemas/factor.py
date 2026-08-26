"""因子层 API 响应 Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class FactorSpecResponse(BaseModel):
    """因子元数据响应，对应 GET /factors/ 端点。

    Attributes:
        factor_id: 因子唯一标识，如 volume_ratio_20d。
        name: 因子中文名称，如 20日量比。
        category: 因子类别：volume/momentum/volatility/flow/valuation。
        version: 语义化版本号。
        description: 计算逻辑说明。
        required_data: 依赖的数据源列表。
        is_active: 是否启用。
    """

    factor_id: str
    name: str
    category: str | None
    version: str
    description: str
    required_data: list[str]
    is_active: bool


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
        factor_id: 因子标识。
        trade_date: 数据日期。
        rows: 横截面数据行列表。
    """

    factor_id: str
    trade_date: str
    rows: list[CrossSectionRow]


class ICPoint(BaseModel):
    """IC 时间序列单点。

    Attributes:
        trade_date: 交易日。
        ic: Rank IC 值。
    """

    trade_date: str
    ic: float


class ICSummary(BaseModel):
    """IC 汇总统计。

    Attributes:
        ic_mean: IC 均值。
        ic_std: IC 标准差。
        ic_ir: IC_IR（IC 均值 / IC 标准差）。
        ic_positive_ratio: IC>0 的比例。
        count: 有效 IC 数据点数量。
    """

    ic_mean: float | None = None
    ic_std: float | None = None
    ic_ir: float | None = None
    ic_positive_ratio: float | None = None
    count: int = 0


class ICResponse(BaseModel):
    """因子 IC 分析响应。

    Attributes:
        factor_id: 因子标识。
        summary: IC 汇总统计。
        series: IC 时间序列。
    """

    factor_id: str
    summary: ICSummary
    series: list[ICPoint]


class CorrelationResponse(BaseModel):
    """因子相关性矩阵响应。

    Attributes:
        factor_ids: 因子标识列表（矩阵行列顺序）。
        matrix: 相关系数二维矩阵。
        index_count: 参与计算的指数数量。
        trade_date: 数据日期。
    """

    factor_ids: list[str]
    matrix: list[list[float]]
    index_count: int
    trade_date: str
