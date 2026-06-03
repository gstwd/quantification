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
    """横截面数据行，包含 ETF 中文名。

    Attributes:
        etf_code: ETF 代码。
        name_cn: ETF 中文简称。
        factor_value_numeric: 因子数值。
        factor_value_text: 因子文本值。
    """

    etf_code: str
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
