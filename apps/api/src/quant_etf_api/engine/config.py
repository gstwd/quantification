"""策略配置 Pydantic 模型。

所有策略通过 JSON 配置定义，无需编写代码。
配置存储在 strategy_config 表的 config_json 字段中。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 引擎配置 schema 版本：配置模型演进时递增并做兼容迁移检测（7.4#3）
#
# v2 起，策略中的因子引用既可以是 factor_aliases 中声明的别名，
# 也可以是模板 ID 本身（使用模板默认参数）。
SUPPORTED_SCHEMA_VERSIONS = {"2"}

# 合法的择时 regime 名称：timing_exposure 与 regime_rules 的 key 均受此约束
RegimeName = Literal["offensive", "neutral", "defensive"]


class TimingThresholds(BaseModel):
    """择时 regime 判定阈值。

    Attributes:
        offensive: 综合得分 >= 此值时判定为进攻 regime。
        defensive: 综合得分 <= 此值时判定为防守 regime。
    """

    offensive: float = Field(default=65.0, ge=0.0, le=100.0)
    defensive: float = Field(default=35.0, ge=0.0, le=100.0)


class TimingConfig(BaseModel):
    """市场择时配置（可选模块）。

    Attributes:
        factors: 因子权重映射，key=因子引用（别名或模板 ID），value=权重。
        transforms: 因子变换函数映射，key=因子引用，value=transform 名称。
        thresholds: regime 判定阈值。
        proxy_index_codes: 择时代理指数代码列表，用于加载市场级因子值。默认沪深300。
    """

    factors: dict[str, float]
    transforms: dict[str, str] = Field(default_factory=dict)
    thresholds: TimingThresholds = Field(default_factory=TimingThresholds)
    proxy_index_codes: list[str] = Field(default_factory=lambda: ["000300"])


class ScoreConfig(BaseModel):
    """资产评分配置。

    Attributes:
        factors: 因子权重映射，key=因子引用（别名或模板 ID），value=权重（支持正负权重）。
            负权重表示"反向"：absolute 模式下按 100 − 得分参与加权，横截面模式下
            按标准化值取反参与加权。
        transforms: 因子变换函数映射，key=因子引用。
        missing_factor_strategy: 因子值缺失时的处理策略。
            ignore=忽略该因子重新归一化权重, zero=按最差处理（该因子记该方向的最低分）,
            exclude=排除该资产。
        scoring_mode: 评分模式。
            absolute=每资产独立评分（默认）, rank=横截面排名分, zscore=横截面 Z-Score。
    """

    factors: dict[str, float]
    transforms: dict[str, str] = Field(default_factory=dict)
    missing_factor_strategy: Literal["ignore", "zero", "exclude"] = "ignore"
    scoring_mode: Literal["absolute", "rank", "zscore"] = "absolute"


class FilterRule(BaseModel):
    """单条过滤规则。

    支持两种比较模式：
    - 因子 vs 固定值：设置 value 字段。
    - 因子 vs 因子：设置 compare_to 字段引用另一个因子引用名。
    value 和 compare_to 必须恰好提供一个。

    Attributes:
        factor: 因子引用（别名或模板 ID）。
        op: 比较操作符：gt / lt / gte / lte / eq / neq / between。
        value: 比较值（固定阈值），between 时为 [min, max]。与 compare_to 二选一。
        compare_to: 被比较的因子引用（如 sma(10) > sma(20) 的两个别名）。与 value 二选一。
        missing_strategy: 因子值缺失时的处理策略。
            fail=规则不满足（默认，与历史行为一致）, pass=规则视为通过,
            exclude=明确排除该资产（语义上与 fail 区分，便于调试定位）。
    """

    factor: str
    op: Literal["gt", "lt", "gte", "lte", "eq", "neq", "between"]
    value: float | list[float] | None = None
    compare_to: str | None = None
    missing_strategy: Literal["fail", "pass", "exclude"] = "fail"


class FilterConfig(BaseModel):
    """过滤配置（可选模块）。

    Attributes:
        logic: 多规则逻辑，AND=全部满足, OR=任一满足。
        rules: 过滤规则列表。
    """

    logic: Literal["AND", "OR"] = "AND"
    rules: list[FilterRule] = Field(default_factory=list)


class FactorAliasConfig(BaseModel):
    """单个因子别名声明：把策略语义名绑定到模板与参数。

    别名承载策略语义（如 trend_fast），模板 ID 与参数承载计算语义。
    同一模板可以在同一策略中以多个别名、不同参数重复出现。

    Attributes:
        template_id: 目标因子模板 ID。
        params: 参数覆盖，键必须是模板 parameter_schema 中声明的参数；
            未给出的参数使用模板默认值。
    """

    template_id: str
    params: dict[str, Any] = Field(default_factory=dict)


class RankConfig(BaseModel):
    """排名配置。

    排名固定按综合得分排序：按单个因子排序与"把该因子放进 score.factors"完全等价，
    再开一个排序入口只会造成"选股按 A、权重按 B"的口径混用，因此不再支持。

    Attributes:
        sort_by: 排序字段，仅支持 score（保留字段以兼容既有配置）。
        order: 排序方向，desc / asc。
        top_n: 取前 N 名，None 表示全部。
        bottom_n: 取后 N 名，与 top_n 二选一。
        momentum_factor: 动量子排名所用因子引用，仅用于结果展示（数值越高排名越前）。
        valuation_factor: 估值子排名所用因子引用，仅用于结果展示（数值越低排名越前）。
    """

    sort_by: Literal["score"] = "score"
    order: Literal["desc", "asc"] = "desc"
    top_n: int | None = Field(default=None, ge=1)
    bottom_n: int | None = Field(default=None, ge=1)
    momentum_factor: str | None = None
    valuation_factor: str | None = None

    @field_validator("sort_by", mode="before")
    @classmethod
    def _reject_non_score_sort(cls, value: Any) -> Any:
        """拒绝 score 以外的排序字段，并给出可操作的替代方案。

        Args:
            value: 原始 sort_by 值。

        Returns:
            原值（仅 score 通过）。

        Raises:
            ValueError: 传入 score 以外的取值时抛出。
        """
        if value is not None and value != "score":
            raise ValueError(
                f"rank.sort_by 仅支持 score（收到 {value!r}）；"
                "如需按某个因子排序，请把该因子加入 score.factors"
            )
        return value


class PortfolioConfig(BaseModel):
    """组合构建配置。

    Attributes:
        method: 权重分配方法，equal_weight / score_weight / winner_take_all。
        timing_exposure: 择时 regime 对应的总仓位上限。
        default_exposure: 无择时信号时的默认总仓位上限，默认 0.50。
    """

    method: Literal["equal_weight", "score_weight", "winner_take_all"]
    timing_exposure: dict[str, float] | None = None
    default_exposure: float = Field(default=0.50, ge=0.0, le=1.0)


class RiskConfig(BaseModel):
    """风控配置（可选模块）。

    Attributes:
        max_asset_weight: 单资产仓位上限。
        max_portfolio_exposure: 组合总仓位上限。
        min_cash_ratio: 最低现金比例。
    """

    max_asset_weight: float = Field(default=0.30, gt=0.0, le=1.0)
    max_portfolio_exposure: float = Field(default=1.0, ge=0.0, le=1.0)
    min_cash_ratio: float = Field(default=0.0, ge=0.0, lt=1.0)


class RebalanceScheduleConfig(BaseModel):
    """单个调仓腿的频率配置。

    严格校验（避免静默失效）：
    - ``frequency`` 只接受 daily / weekly / biweekly / monthly——历史上未知频率会在
      调度器末尾 ``return True``，静默退化为每日调仓（换手与成本最大）；
    - ``day_of_week`` 0-4（周一至周五）；``day_of_month`` 1-31，超出当月天数时按
      当月最后一日处理（不再整月不调仓）；
    - ``biweekly`` 必须显式声明 ``week_parity``：以 2016-01-04（周一）所在周为第 1 周
      连续计数，奇偶决定"哪一周调仓"；不使用 ISO 周号，避免第 53 周与次年第 1 周同为
      奇数周导致连续两周调仓。不提供默认值以免"以为配了双周、实际按另一个奇偶周执行"。

    Attributes:
        frequency: 调仓频率，daily / weekly / biweekly / monthly。
        day_of_week: 周度/双周调仓日（0=周一, 4=周五），默认 4。
        week_parity: 双周频率的周次奇偶（odd=奇数周, even=偶数周，按锚点周连续计数），
            仅 biweekly 生效。
        day_of_month: 月度调仓日（1-31，建议 ≤28），默认 1。
    """

    frequency: Literal["daily", "weekly", "biweekly", "monthly"] = "daily"
    day_of_week: int | None = Field(default=None, ge=0, le=4)
    week_parity: Literal["odd", "even"] | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)

    @model_validator(mode="after")
    def _validate_biweekly_parity(self) -> "RebalanceScheduleConfig":
        """双周频率必须显式声明 odd/even 周次。

        Returns:
            校验通过的原对象。

        Raises:
            ValueError: 频率为 biweekly 但未指定 week_parity 时抛出。
        """
        if self.frequency == "biweekly" and self.week_parity is None:
            raise ValueError("biweekly 频率必须指定 week_parity 为 odd 或 even")
        return self


class RebalanceConfig(BaseModel):
    """双腿调仓配置（可选模块）。

    启用本模块时**两条腿必选**（都有默认值，因此 JSON 中可只给一条腿，另一条腿
    按"与已给出的那条一致"归一化）：

    - ``selection`` 选股腿：重建组合成分与权重；
    - ``risk`` 风险腿：只把**现有成分**等比缩放到择时目标总仓位，不引入新成分。

    两腿频率一致时（旧配置升级后即如此）行为与改造前完全一致：每个调仓日都按
    "新成分 + 择时目标仓位"重建；只有两腿频率不同时才会出现"仅换成分保持仓位"
    或"仅缩放仓位"的差异化日程。

    旧平铺配置（``frequency`` / ``day_of_week`` / ``day_of_month`` 直接挂在
    ``rebalance`` 下）会被升级为**两腿同频**，从而保持历史回测口径不变。
    """

    selection: RebalanceScheduleConfig = Field(default_factory=RebalanceScheduleConfig)
    risk: RebalanceScheduleConfig = Field(default_factory=RebalanceScheduleConfig)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_config(cls, value: Any) -> Any:
        """把旧平铺配置升级为双腿配置，并让缺省的一条腿与另一条保持一致。

        Args:
            value: 待解析的 ``rebalance`` 原始值（dict 或已是模型实例）。

        Returns:
            归一化后的原始值：显式的 selection/risk 保持不变，仅有一条腿时另一条
            复制该腿，旧平铺字段则同时作为两条腿的频率。
        """
        if not isinstance(value, dict):
            return value
        # 旧平铺口径：frequency/day_of_week/... 直接写在 rebalance 下
        if "frequency" in value:
            legacy = {
                key: value[key]
                for key in ("frequency", "day_of_week", "week_parity", "day_of_month")
                if key in value
            }
            return {"selection": legacy, "risk": dict(legacy)}
        normalized = dict(value)
        # 双腿必选：只给出其中一条腿时，另一条按相同日程补齐（不改变单腿语义）
        if "selection" not in normalized and "risk" in normalized:
            normalized["selection"] = normalized["risk"]
        if "risk" not in normalized and "selection" in normalized:
            normalized["risk"] = normalized["selection"]
        return normalized

    # 兼容旧的服务调用与外部代码；新代码应使用 selection / risk。
    @property
    def frequency(self) -> str:
        return self.selection.frequency

    @property
    def day_of_week(self) -> int | None:
        return self.selection.day_of_week

    @property
    def day_of_month(self) -> int | None:
        return self.selection.day_of_month


class RegimeRuleConfig(BaseModel):
    """单个 regime 下的策略配置覆盖。

    用于条件化策略逻辑：不同市场状态（offensive/neutral/defensive）使用不同的
    评分、过滤、排名和组合配置。未指定的字段保持默认配置值。

    Attributes:
        score: 评分配置覆盖，None 表示使用默认 score。
        filters: 过滤配置覆盖，None 表示使用默认 filters。
        rank: 排名配置覆盖，None 表示使用默认 rank。
        portfolio: 组合配置覆盖，None 表示使用默认 portfolio。
    """

    score: ScoreConfig | None = None
    filters: FilterConfig | None = None
    rank: RankConfig | None = None
    portfolio: PortfolioConfig | None = None


class StrategyConfig(BaseModel):
    """完整策略配置。

    Attributes:
        strategy_id: 策略唯一标识。
        display_name: 策略中文名称。
        version: 版本号。
        schema_version: 引擎配置 schema 版本，用于配置模型演进时的兼容检测。
        description: 策略描述。
        frequency: 策略标注频率（元数据，**不控制调仓**；实际调仓频率见
            ``rebalance.selection.frequency``）。保留该字段是为了列表/标签展示的兼容。
        timing: 择时配置，None 表示无择时。
        score: 评分配置（必填）。
        filters: 过滤配置，None 表示无过滤。
        rank: 排名配置。
        portfolio: 组合配置；兼容旧配置时缺失字段自动补为默认等权配置。
        risk: 风控配置，None 表示无风控。
        rebalance: 调仓配置，None 表示每日调仓。
        factor_aliases: 因子别名声明，key=别名，value=模板与参数。
            评分/过滤/择时/排名中未在此声明的引用一律按模板 ID 解释。
    """

    strategy_id: str
    display_name: str
    version: str = "1.0.0"
    schema_version: str = "2"
    description: str = ""
    frequency: str = "daily"
    index_codes: list[str] = Field(
        default_factory=list,
        description=(
            "指定指数代码列表（benchmark_index 中由用户添加的指数），"
            "非空时仅对这些指数运行策略"
        ),
    )
    factor_aliases: dict[str, FactorAliasConfig] = Field(
        default_factory=dict,
        description="因子别名声明，key=别名，value=目标模板与参数覆盖",
    )
    timing: TimingConfig | None = None
    score: ScoreConfig
    filters: FilterConfig | None = None
    rank: RankConfig = Field(default_factory=RankConfig)
    portfolio: PortfolioConfig = Field(
        default_factory=lambda: PortfolioConfig(method="equal_weight"),
    )
    risk: RiskConfig | None = None
    rebalance: RebalanceConfig | None = None
    regime_rules: dict[RegimeName, RegimeRuleConfig] = Field(
        default_factory=dict,
        description="regime 条件化配置，key=regime 名称（offensive/neutral/defensive），value=该 regime 下的配置覆盖",
    )


def iter_factor_refs(config: StrategyConfig) -> list[tuple[str, str, str]]:
    """列出策略各模块引用的全部因子（不去重），作为引用收集的唯一实现。

    因子加载、配置校验（引用存在性与 usage 匹配）、未使用别名提示都依赖同一份
    引用清单；此前各处各写一遍遍历，新增模块时极易漏掉某一处。

    Args:
        config: 已解析的策略配置。

    Returns:
        ``(模块 key, 中文位置标签, 因子引用)`` 列表；模块 key 取 score / timing /
        filter / rank，与因子模板 ``usage`` 取值一致。
    """
    refs: list[tuple[str, str, str]] = []
    refs.extend(("score", "评分", ref) for ref in config.score.factors)
    if config.timing:
        refs.extend(("timing", "择时", ref) for ref in config.timing.factors)
    if config.filters:
        refs.extend(("filter", "过滤", ref) for ref in filter_rule_refs(config.filters))
    for ref in (config.rank.momentum_factor, config.rank.valuation_factor):
        if ref:
            refs.append(("rank", "排名子因子", ref))
    for regime_rule in config.regime_rules.values():
        if regime_rule.score:
            refs.extend(("score", "regime 评分", ref) for ref in regime_rule.score.factors)
        if regime_rule.filters:
            refs.extend(
                ("filter", "regime 过滤", ref) for ref in filter_rule_refs(regime_rule.filters)
            )
        if regime_rule.rank:
            for ref in (regime_rule.rank.momentum_factor, regime_rule.rank.valuation_factor):
                if ref:
                    refs.append(("rank", "regime 排名子因子", ref))
    return refs


def filter_rule_refs(filters: FilterConfig) -> list[str]:
    """列出过滤配置引用的因子（含跨因子比较的 compare_to）。

    Args:
        filters: 过滤配置。

    Returns:
        按规则顺序排列的因子引用列表（不去重）。
    """
    refs: list[str] = []
    for rule in filters.rules:
        refs.append(rule.factor)
        if rule.compare_to:
            refs.append(rule.compare_to)
    return refs
