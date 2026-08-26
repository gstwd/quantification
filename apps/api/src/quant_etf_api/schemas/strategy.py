"""策略相关 Schema：策略详情、配置 CRUD、资产配置响应。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from quant_etf_api.schemas.backtest import BacktestWarning


class StrategySummary(BaseModel):
    """策略摘要，用于列表展示。

    Attributes:
        strategy_id: 策略唯一标识。
        display_name: 策略中文名称。
        version: 版本号。
        frequency: 运行频率。
        description: 策略描述。
        status: 状态。
        is_starred: 是否星标关注。
        index_codes: 策略绑定的指数代码列表，空列表表示全指数通用。
    """

    strategy_id: str
    display_name: str
    version: str
    frequency: str
    description: str
    status: str = "active"
    is_starred: bool = False
    index_codes: list[str] = Field(default_factory=list)


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
        data_date: 本次决策所用因子数据的交易日，用于展示数据新鲜度。
        pipeline_detail: 管线调试详情，含评分分解、过滤明细、择时因子等中间数据。
        warnings: 执行过程中检测到的提示（如因子缺失），无则空列表。
    """

    timing: dict[str, Any]
    rankings: list[dict[str, Any]]
    plan: dict[str, Any]
    data_date: date | None = None
    pipeline_detail: dict[str, Any] | None = None
    warnings: list[BacktestWarning] = Field(default_factory=list)


class StarredStrategyItem(BaseModel):
    """星标策略执行摘要，用于总览页展示。

    Attributes:
        strategy_id: 策略唯一标识。
        display_name: 策略中文名称。
        frequency: 运行频率。
        is_rebalance_day: 是否为调仓日。
        rebalance_frequency: 调仓频率。
        rebalance_day_of_week: 周度调仓的星期几（0=周一）。
        rebalance_day_of_month: 月度调仓的日期（1-31）。
        timing: 择时信号，含 regime、confidence、label。
        rankings: 资产排名列表。
        plan: 仓位分配方案。
        data_date: 所用数据的最新交易日。
    """

    strategy_id: str
    display_name: str
    frequency: str
    is_rebalance_day: bool
    rebalance_frequency: str | None = None
    rebalance_day_of_week: int | None = None
    rebalance_day_of_month: int | None = None
    timing: dict[str, Any] | None = None
    rankings: list[dict[str, Any]] = Field(default_factory=list)
    plan: dict[str, Any] = Field(default_factory=dict)
    data_date: date | None = None


class StarredSummaryResponse(BaseModel):
    """星标策略执行摘要响应。

    Attributes:
        trade_date: 执行日期。
        items: 各星标策略的执行摘要列表。
    """

    trade_date: date
    items: list[StarredStrategyItem] = Field(default_factory=list)


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
