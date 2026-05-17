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
    """

    factor_id: str
    name: str
    category: str
    version: str
    description: str
    required_data: list[str]
