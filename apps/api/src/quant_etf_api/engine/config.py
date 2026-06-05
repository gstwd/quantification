"""策略配置 Pydantic 模型。

所有策略通过 JSON 配置定义，无需编写代码。
配置存储在 strategy_config 表的 config_json 字段中。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TimingThresholds(BaseModel):
    """择时 regime 判定阈值。

    Attributes:
        offensive: 综合得分 >= 此值时判定为进攻 regime。
        defensive: 综合得分 <= 此值时判定为防守 regime。
    """

    offensive: float = 65.0
    defensive: float = 35.0


class TimingConfig(BaseModel):
    """市场择时配置（可选模块）。

    Attributes:
        factors: 因子权重映射，key=factor_id, value=权重。
        transforms: 因子变换函数映射，key=factor_id, value=transform 名称。
        thresholds: regime 判定阈值。
    """

    factors: dict[str, float]
    transforms: dict[str, str] = Field(default_factory=dict)
    thresholds: TimingThresholds = Field(default_factory=TimingThresholds)


class ScoreConfig(BaseModel):
    """资产评分配置。

    Attributes:
        factors: 因子权重映射，key=factor_id, value=权重（支持正负权重）。
        transforms: 因子变换函数映射。
        missing_factor_strategy: 因子值缺失时的处理策略。
            ignore=忽略该因子重新归一化权重, zero=按 0 处理, exclude=排除该资产。
    """

    factors: dict[str, float]
    transforms: dict[str, str] = Field(default_factory=dict)
    missing_factor_strategy: str = "ignore"


class FilterRule(BaseModel):
    """单条过滤规则。

    Attributes:
        factor: 因子标识。
        op: 比较操作符：gt / lt / gte / lte / eq / neq / between。
        value: 比较值，between 时为 [min, max]。
    """

    factor: str
    op: str
    value: float | list[float]


class FilterConfig(BaseModel):
    """过滤配置（可选模块）。

    Attributes:
        logic: 多规则逻辑，AND=全部满足, OR=任一满足。
        rules: 过滤规则列表。
    """

    logic: str = "AND"
    rules: list[FilterRule] = Field(default_factory=list)


class RankConfig(BaseModel):
    """排名配置。

    Attributes:
        sort_by: 排序字段，默认 score。
        order: 排序方向，desc / asc。
        top_n: 取前 N 名，None 表示全部。
        bottom_n: 取后 N 名，与 top_n 二选一。
    """

    sort_by: str = "score"
    order: str = "desc"
    top_n: int | None = None
    bottom_n: int | None = None


class PortfolioConfig(BaseModel):
    """组合构建配置（可选模块，缺失时为信号模式）。

    Attributes:
        method: 权重分配方法，equal_weight / score_weight。
        timing_exposure: 择时 regime 对应的总仓位上限。
    """

    method: str
    timing_exposure: dict[str, float] | None = None


class RiskConfig(BaseModel):
    """风控配置（可选模块）。

    Attributes:
        max_asset_weight: 单资产仓位上限。
        max_portfolio_exposure: 组合总仓位上限。
        min_cash_ratio: 最低现金比例。
    """

    max_asset_weight: float = 0.30
    max_portfolio_exposure: float = 1.0
    min_cash_ratio: float = 0.0


class RebalanceConfig(BaseModel):
    """调仓配置（可选模块）。

    Attributes:
        frequency: 调仓频率，daily / weekly / monthly。
        day_of_week: 周度调仓日（0=周一, 4=周五）。
        day_of_month: 月度调仓日。
    """

    frequency: str = "daily"
    day_of_week: int | None = None
    day_of_month: int | None = None


class StrategyConfig(BaseModel):
    """完整策略配置。

    Attributes:
        strategy_id: 策略唯一标识。
        display_name: 策略中文名称。
        version: 版本号。
        description: 策略描述。
        frequency: 运行频率。
        asset_scope: 资产范围。
        timing: 择时配置，None 表示无择时。
        score: 评分配置（必填）。
        filters: 过滤配置，None 表示无过滤。
        rank: 排名配置。
        portfolio: 组合配置，None 表示信号模式。
        risk: 风控配置，None 表示无风控。
        rebalance: 调仓配置，None 表示每日调仓。
    """

    strategy_id: str
    display_name: str
    version: str = "1.0.0"
    description: str = ""
    frequency: str = "daily"
    asset_scope: str = "a_share_etf"
    timing: TimingConfig | None = None
    score: ScoreConfig
    filters: FilterConfig | None = None
    rank: RankConfig = Field(default_factory=RankConfig)
    portfolio: PortfolioConfig | None = None
    risk: RiskConfig | None = None
    rebalance: RebalanceConfig | None = None
