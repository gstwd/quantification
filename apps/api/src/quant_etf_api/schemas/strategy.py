"""策略相关 Schema：策略详情、配置 CRUD、资产配置响应。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StrategySummary(BaseModel):
    """策略摘要，用于列表展示。

    Attributes:
        strategy_id: 策略唯一标识。
        display_name: 策略中文名称。
        version: 版本号。
        frequency: 运行频率。
        description: 策略描述。
        status: 状态。
    """

    strategy_id: str
    display_name: str
    version: str
    frequency: str
    description: str
    status: str = "active"


class StrategyDetail(StrategySummary):
    """策略详情，包含完整配置。

    Attributes:
        config_json: 完整策略配置 JSON。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    config_json: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StrategyConfigCreate(BaseModel):
    """创建策略配置请求。

    Attributes:
        strategy_id: 策略唯一标识。
        display_name: 策略中文名称。
        version: 版本号。
        description: 策略描述。
        frequency: 运行频率。
        config_json: 完整策略配置 JSON。
    """

    strategy_id: str
    display_name: str
    version: str = "1.0.0"
    description: str = ""
    frequency: str = "daily"
    config_json: dict[str, Any]


class StrategyConfigUpdate(BaseModel):
    """更新策略配置请求。

    Attributes:
        display_name: 策略中文名称。
        version: 版本号。
        description: 策略描述。
        frequency: 运行频率。
        config_json: 完整策略配置 JSON。
        status: 状态。
    """

    display_name: str | None = None
    version: str | None = None
    description: str | None = None
    frequency: str | None = None
    config_json: dict[str, Any] | None = None
    status: str | None = None


class AllocationResponse(BaseModel):
    """资产配置决策管线响应。

    Attributes:
        timing: 择时信号，含 regime、confidence、label、factors。
        rankings: 资产轮动排名列表。
        plan: 仓位分配方案，含 positions、total_exposure、cash_ratio、reasoning。
    """

    timing: dict[str, Any]
    rankings: list[dict[str, Any]]
    plan: dict[str, Any]


class StrategyValidationResult(BaseModel):
    """策略配置校验结果。

    Attributes:
        valid: 是否有效。
        errors: 错误信息列表。
        warnings: 警告信息列表。
    """

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
