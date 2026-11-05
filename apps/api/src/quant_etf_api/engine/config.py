"""策略配置 Pydantic 模型。

所有策略通过 JSON 配置定义，无需编写代码。
配置存储在 strategy_config 表的 config_json 字段中。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 引擎配置 schema 版本：配置模型演进时递增并做兼容迁移检测（7.4#3）
#
# schema v2：策略中的因子引用既可以是 factor_aliases 中声明的别名，
# 也可以是模板 ID 本身（使用模板默认参数）。
SUPPORTED_SCHEMA_VERSIONS = {"2"}

# 合法的择时 regime 名称：timing_exposure 与 regime_rules 的 key 均受此约束
RegimeName = Literal["offensive", "neutral", "defensive"]

# 择时因子全部缺失时的回退策略（见 TimingConfig.on_degraded）
TimingDegradedPolicy = Literal["neutral", "defensive", "hold_previous"]

# 择时综合方式：weighted=因子加权综合分过阈值（默认）；rules=按进攻/防守规则组判定
TimingCombineMode = Literal["weighted", "rules"]


class TimingThresholds(BaseModel):
    """择时 regime 判定阈值。

    Attributes:
        offensive: 综合得分 >= 此值时判定为进攻 regime。
        defensive: 综合得分 <= 此值时判定为防守 regime。
    """

    offensive: float = Field(default=65.0, ge=0.0, le=100.0)
    defensive: float = Field(default=35.0, ge=0.0, le=100.0)


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


# 规则组最大嵌套层数（顶层算第 1 层）：足以表达"(A 且 B) 或 C"，避免不可读的深层树
MAX_RULE_GROUP_DEPTH = 3


class FilterConfig(BaseModel):
    """过滤配置 / 规则组（过滤、择时规则组、规则模式档位、条件仓位上限共用）。

    ``rules`` 与 ``groups`` 一起按 ``logic`` 组合：AND=全部满足，OR=任一满足。
    子规则组可再嵌套（最多 :data:`MAX_RULE_GROUP_DEPTH` 层），用于表达
    "(RSI ≤ 25 且 年线乖离 ≤ −10) 或 年线乖离 ≤ −15" 这类条件。

    Attributes:
        logic: 多规则逻辑，AND=全部满足, OR=任一满足。
        rules: 过滤规则列表。
        groups: 子规则组列表（与 rules 同级参与组合）；默认空，存量配置口径不变。
    """

    logic: Literal["AND", "OR"] = "AND"
    rules: list[FilterRule] = Field(default_factory=list)
    groups: list[FilterConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_depth(self) -> FilterConfig:
        """限制嵌套层数。"""
        if self.depth() > MAX_RULE_GROUP_DEPTH:
            raise ValueError(f"规则组最多嵌套 {MAX_RULE_GROUP_DEPTH} 层")
        return self

    def depth(self) -> int:
        """规则组的嵌套层数（只有 rules 时为 1）。"""
        return 1 + max((group.depth() for group in self.groups), default=0)

    @property
    def is_empty(self) -> bool:
        """没有任何规则（含子组）。"""
        return not self.rules and all(group.is_empty for group in self.groups)


class TimingRulesConfig(BaseModel):
    """规则模式（``timing.combine=rules``）下的进攻 / 防守规则组。

    判定顺序为"风险优先"：防守组满足 → defensive；否则进攻组满足 → offensive；
    都不满足 → neutral（"无信号"，可配合 ``state.hold_on_neutral`` 维持前值）。
    两组各自独立配置 AND / OR，可表达研报常见的进出场不对称，例如进攻要求
    "趋势多头 且 动量上行"（AND），防守只要"任一转空"（OR）。

    规则直接比较市场级因子的**原值**（与过滤规则同一口径，不经 transforms），
    取值来自择时代理指数；缺失按各规则的 ``missing_strategy`` 处理
    （pass=视为满足，fail/exclude=视为不满足）。

    Attributes:
        offensive: 进攻规则组；None 表示不会判定为进攻。
        defensive: 防守规则组；None 表示不会判定为防守。
    """

    offensive: FilterConfig | None = None
    defensive: FilterConfig | None = None


class TimingTierConfig(BaseModel):
    """择时仓位档位（``timing.tiers`` 的一项）：命中条件 → 目标总仓位。

    档位按列表顺序匹配，**首个命中**的档位决定目标总仓位（取代按 regime 查
    ``portfolio.timing_exposure``），用于表达 0/25/50/75/100 这类多档加减仓。
    两种综合方式使用不同的命中条件：

    - 加权模式（combine=weighted）：综合分 ≥ ``min_score`` 命中；regime 仍由
      ``thresholds`` 判定，档位只决定仓位；
    - 规则模式（combine=rules）：``logic`` + ``rules`` 规则组满足即命中（与过滤
      规则同一口径、比较原值），命中档位的 ``regime`` 即当日原始 regime。

    都未命中时回退原口径（按 regime 查 ``portfolio.timing_exposure`` / 默认三档表）。

    Attributes:
        label: 档位展示名，如"深度超跌"；空串时按序号展示。
        exposure: 命中该档时的目标总仓位（0-1，组合级风控上限之前）。
        min_score: 加权模式的命中下限（综合分 ≥ 该值命中，0-100）。
        regime: 规则模式下命中该档时的 regime（供状态层、regime_rules 与择时统计使用）。
        logic: 规则模式的多规则逻辑，AND=全部满足, OR=任一满足。
        rules: 规则模式的命中规则（与过滤规则同结构）。
    """

    label: str = Field(default="", max_length=40)
    exposure: float | None = Field(default=None, ge=0.0, le=1.0)
    hold: bool = False
    exposure_delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    min_exposure: float = Field(default=0.0, ge=0.0, le=1.0)
    max_exposure: float = Field(default=1.0, ge=0.0, le=1.0)
    min_score: float | None = Field(default=None, ge=0.0, le=100.0)
    regime: RegimeName | None = None
    logic: Literal["AND", "OR"] = "AND"
    rules: list[FilterRule] = Field(default_factory=list)
    groups: list[FilterConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_exposure_mode(self) -> TimingTierConfig:
        """仓位方式三选一：固定仓位 / 维持上一仓位 / 相对上一仓位增减。"""
        modes = [self.exposure is not None, self.hold, self.exposure_delta is not None]
        if sum(modes) != 1:
            raise ValueError("档位必须且只能设置 exposure、hold、exposure_delta 之一")
        if self.min_exposure > self.max_exposure:
            raise ValueError("档位 min_exposure 不能大于 max_exposure")
        return self

    @property
    def is_dynamic(self) -> bool:
        """仓位依赖上一决策日的有效仓位（hold 或 exposure_delta）。"""
        return self.hold or self.exposure_delta is not None

    def exposure_text(self) -> str:
        """仓位方式的展示文本（如 "50%"、"维持"、"+25%（0%~100%）"）。"""
        if self.hold:
            return "维持"
        if self.exposure_delta is not None:
            return (
                f"{self.exposure_delta:+.0%}（{self.min_exposure:.0%}~{self.max_exposure:.0%}）"
            )
        return f"{self.exposure:.0%}"

    def display_label(self, index: int) -> str:
        """返回档位展示名（未命名时按序号）。

        Args:
            index: 档位在 ``timing.tiers`` 中的序号（0 起）。

        Returns:
            展示名。
        """
        return self.label or f"档位{index + 1}"

    def as_rule_group(self) -> FilterConfig:
        """把规则模式档位的命中条件转为规则组（复用过滤规则的求值与校验）。

        Returns:
            等价的 FilterConfig。
        """
        return FilterConfig(logic=self.logic, rules=self.rules, groups=self.groups)


class TimingExposureCapConfig(BaseModel):
    """条件仓位上限（``timing.exposure_caps`` 的一项）：规则组满足时封顶目标总仓位。

    规则比较市场级因子原值（取值来自择时代理，与规则模式同一口径与缺失语义），
    多条上限同时命中时取最小值；上限在档位 / regime 仓位之后、组合级风控之前施加，
    例如"5 年估值分位 ≥ 80 时总仓位不超过 50%"。

    Attributes:
        label: 展示名。
        max_exposure: 命中时的总仓位上限（0-1）。
        logic: 多规则逻辑。
        rules: 命中规则。
        groups: 子规则组。
    """

    label: str = Field(default="", max_length=40)
    max_exposure: float = Field(ge=0.0, le=1.0)
    logic: Literal["AND", "OR"] = "AND"
    rules: list[FilterRule] = Field(default_factory=list)
    groups: list[FilterConfig] = Field(default_factory=list)

    def as_rule_group(self) -> FilterConfig:
        """转为规则组（复用规则求值与校验）。"""
        return FilterConfig(logic=self.logic, rules=self.rules, groups=self.groups)

    def display_label(self, index: int) -> str:
        """返回展示名（未命名时按序号）。"""
        return self.label or f"仓位上限{index + 1}"


class TimingStateConfig(BaseModel):
    """择时状态层配置：在原始 regime 之上施加滞回、无信号维持、确认与最短持有。

    默认值下状态层为恒等变换（有效 regime 等于原始 regime），存量配置口径不变。
    天数均按**决策日**（交易日）计；实时路径回放最近
    ``engine.timing.TIMING_STATE_REPLAY_DAYS`` 个交易日重建状态，与回测同一实现。

    Attributes:
        hysteresis: 滞回带宽（综合分点数，仅加权模式生效）：已处于进攻时综合分
            不低于 ``offensive − hysteresis`` 即保持进攻；防守对称。0 表示不启用。
        hold_on_neutral: 候选 regime 为中性（无信号）时沿用上一有效 regime。
        confirm_days: 切换到新 regime 所需的连续确认决策日数，1 表示当日即切换。
        min_hold_days: 有效 regime 切换后最短持有的决策日数，0 表示不限制。
        max_exposure_step: 档位决定的目标仓位每个决策日最多变动的幅度（0-1），
            如 0.25 表示"每次最多加 / 减一档"；None 表示不限制。只约束由 ``tiers``
            决定的仓位（未命中档位、按 regime 仓位表时不受约束）。
    """

    hysteresis: float = Field(default=0.0, ge=0.0, le=50.0)
    hold_on_neutral: bool = False
    confirm_days: int = Field(default=1, ge=1, le=60)
    min_hold_days: int = Field(default=0, ge=0, le=250)
    max_exposure_step: float | None = Field(default=None, gt=0.0, le=1.0)

    @property
    def is_identity(self) -> bool:
        """状态层是否为恒等变换（实时路径据此跳过历史回放）。"""
        return (
            self.hysteresis <= 0
            and not self.hold_on_neutral
            and self.confirm_days <= 1
            and self.min_hold_days <= 0
            and self.max_exposure_step is None
        )


class TimingConfig(BaseModel):
    """市场择时配置（可选模块）。

    Attributes:
        combine: 综合方式。weighted=因子加权综合分过阈值（默认）；
            rules=按 ``rules`` 中的进攻 / 防守规则组判定。
        factors: 加权模式的因子权重映射，key=因子引用（别名或模板 ID），value=权重。
            规则模式下可为空（规则引用的因子另行收集）。
        transforms: 因子变换函数映射，key=因子引用，value=transform 名称。
            综合分在 0-100 空间合成：原值不在 0-100 的因子必须配置变换函数。
            规则模式比较原值，不使用变换。
        thresholds: 加权模式的 regime 判定阈值。
        rules: 规则模式的进攻 / 防守规则组。
        proxy_index_codes: 择时代理指数代码列表，用于加载市场级因子值。默认沪深300。
            按顺序选第一个让全部（同组）择时因子都有值的代理；无代理全覆盖时逐因子回退。
        factor_proxies: 按因子指定代理，key=因子引用，value=该因子的代理指数代码列表
            （同样按顺序取值）；未列出的因子使用 ``proxy_index_codes``。
        on_degraded: 择时因子全部缺失（降级）时采用的 regime：
            neutral=按中性（默认，兼容历史口径）、defensive=按防守、
            hold_previous=沿用上一决策日的有效 regime（实时路径回放历史得到）。
        state: 状态层配置（滞回、无信号维持、确认天数、最短持有）。
        tiers: 多档仓位（按顺序匹配，首个命中的档位决定目标总仓位）；空列表表示
            不启用，按 regime 查 ``portfolio.timing_exposure``（存量口径）。规则模式
            下与 ``rules.offensive/defensive`` 互斥。档位可用 ``hold`` 维持上一仓位、
            ``exposure_delta`` 相对上一仓位增减。
        tier_base_exposure: 动态档位（hold / exposure_delta）在没有上一仓位时的起点
            （0-1）；配置了动态档位时必填。
        exposure_caps: 条件仓位上限；命中的上限取最小值封顶目标总仓位。
    """

    combine: TimingCombineMode = "weighted"
    factors: dict[str, float] = Field(default_factory=dict)
    transforms: dict[str, str] = Field(default_factory=dict)
    thresholds: TimingThresholds = Field(default_factory=TimingThresholds)
    rules: TimingRulesConfig | None = None
    proxy_index_codes: list[str] = Field(default_factory=lambda: ["000300"])
    factor_proxies: dict[str, list[str]] = Field(default_factory=dict)
    on_degraded: TimingDegradedPolicy = "neutral"
    state: TimingStateConfig = Field(default_factory=TimingStateConfig)
    tiers: list[TimingTierConfig] = Field(default_factory=list)
    tier_base_exposure: float | None = Field(default=None, ge=0.0, le=1.0)
    exposure_caps: list[TimingExposureCapConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_dynamic_tiers(self) -> TimingConfig:
        """动态档位需要起点仓位。"""
        if self.has_dynamic_tiers and self.tier_base_exposure is None:
            raise ValueError(
                "timing.tiers 含 hold / exposure_delta 档位时必须设置 timing.tier_base_exposure"
            )
        return self

    @property
    def has_dynamic_tiers(self) -> bool:
        """是否存在依赖上一仓位的档位。"""
        return any(tier.is_dynamic for tier in self.tiers)

    def cap_refs(self) -> list[str]:
        """条件仓位上限引用的因子（按顺序，不去重）。"""
        refs: list[str] = []
        for cap in self.exposure_caps:
            refs.extend(filter_rule_refs(cap.as_rule_group()))
        return refs

    def market_refs(self) -> list[str]:
        """需要按择时代理现算的全部市场级因子（择时判定 + 条件仓位上限，去重保序）。"""
        return list(dict.fromkeys([*self.factor_refs(), *self.cap_refs()]))

    def rule_refs(self) -> list[str]:
        """规则模式引用的因子（含 compare_to，按规则顺序，不去重）。

        规则组（防守、进攻）在前，规则模式档位的命中规则按档位顺序在后。

        Returns:
            因子引用列表；加权模式或未配置规则时为空。
        """
        if self.combine != "rules":
            return []
        refs: list[str] = []
        if self.rules is not None:
            for group in (self.rules.defensive, self.rules.offensive):
                if group is not None:
                    refs.extend(filter_rule_refs(group))
        for tier in self.tiers:
            refs.extend(filter_rule_refs(tier.as_rule_group()))
        return refs

    def factor_refs(self) -> list[str]:
        """择时实际使用的全部因子引用（去重、保序）。

        加权模式为 ``factors`` 的键；规则模式为规则组与档位规则引用的因子。

        Returns:
            因子引用列表。
        """
        refs = list(self.factors) if self.combine == "weighted" else self.rule_refs()
        return list(dict.fromkeys(refs))

    def proxies_for(self, ref: str) -> list[str]:
        """返回某个择时因子使用的代理指数代码列表。

        Args:
            ref: 因子引用。

        Returns:
            ``factor_proxies`` 中的单独配置，未配置时为 ``proxy_index_codes``。
        """
        return list(self.factor_proxies.get(ref) or self.proxy_index_codes)

    def proxy_groups(self) -> list[tuple[list[str], list[str]]]:
        """按代理列表把择时因子分组（供代理选择纯函数使用）。

        Returns:
            ``(代理指数代码列表, 因子引用列表)`` 列表；默认代理组在前。
        """
        groups: dict[tuple[str, ...], list[str]] = {tuple(self.proxy_index_codes): []}
        for ref in self.market_refs():
            groups.setdefault(tuple(self.proxies_for(ref)), []).append(ref)
        return [(list(codes), refs) for codes, refs in groups.items() if refs]

    def all_proxy_codes(self) -> list[str]:
        """择时涉及的全部代理指数代码（去重、保序），供行情与因子预加载。

        Returns:
            代理指数代码列表。
        """
        codes = list(self.proxy_index_codes)
        for ref in self.market_refs():
            codes.extend(self.proxies_for(ref))
        return list(dict.fromkeys(codes))

    def tier_specs(self) -> list[tuple[str, float | None]]:
        """多档仓位的 ``(展示名, 仓位)`` 列表（供择时统计按档位分组）。

        Returns:
            按配置顺序的列表；动态档位（hold / exposure_delta）的仓位为 None；
            未启用档位时为空。
        """
        return [(tier.display_label(index), tier.exposure) for index, tier in enumerate(self.tiers)]

    @property
    def needs_history(self) -> bool:
        """择时判定是否依赖历史（状态层非恒等、降级时沿用前值或存在动态档位）。"""
        return (
            not self.state.is_identity
            or self.on_degraded == "hold_previous"
            or self.has_dynamic_tiers
        )


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
        buffer_n: 排名缓冲带（只对 top_n 生效）：已持有资产排名不低于 top_n + buffer_n
            时继续保留，剩余名额按排名补足；None 表示不启用（每次都取严格前 N 名）。
    """

    sort_by: Literal["score"] = "score"
    order: Literal["desc", "asc"] = "desc"
    top_n: int | None = Field(default=None, ge=1)
    bottom_n: int | None = Field(default=None, ge=1)
    momentum_factor: str | None = None
    valuation_factor: str | None = None
    buffer_n: int | None = Field(default=None, ge=1, le=50)

    @model_validator(mode="after")
    def _buffer_requires_top_n(self) -> "RankConfig":
        """缓冲带只对 top_n 截取有意义。

        Returns:
            校验通过的原对象。

        Raises:
            ValueError: 设置了 buffer_n 但未设置 top_n 时抛出。
        """
        if self.buffer_n is not None and self.top_n is None:
            raise ValueError("rank.buffer_n 需要同时设置 rank.top_n")
        return self

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


PortfolioMethod = Literal[
    "equal_weight", "score_weight", "winner_take_all", "inverse_vol", "risk_parity"
]


class PortfolioConfig(BaseModel):
    """组合构建配置。

    Attributes:
        method: 权重分配方法：equal_weight / score_weight / winner_take_all /
            inverse_vol（按 ``vol_factor`` 的倒数加权）/ risk_parity（按近
            ``cov_window`` 个交易日收益协方差求等风险贡献）。
        timing_exposure: 择时 regime 对应的总仓位上限。
        default_exposure: 无择时信号时的默认总仓位上限，默认 0.50。
        vol_factor: inverse_vol 使用的波动率因子引用（如 volatility / return_std 别名），
            数值须为正；该因子缺失的资产在评分前剔除。
        cov_window: risk_parity 的协方差窗口（交易日，20-250）；历史不足时退回等权。
    """

    method: PortfolioMethod
    timing_exposure: dict[str, float] | None = None
    default_exposure: float = Field(default=0.50, ge=0.0, le=1.0)
    vol_factor: str | None = None
    cov_window: int = Field(default=60, ge=20, le=250)

    @model_validator(mode="after")
    def _inverse_vol_requires_factor(self) -> "PortfolioConfig":
        """inverse_vol 必须声明波动率因子。

        Returns:
            校验通过的原对象。

        Raises:
            ValueError: method=inverse_vol 但未给 vol_factor 时抛出。
        """
        if self.method == "inverse_vol" and not self.vol_factor:
            raise ValueError("portfolio.method=inverse_vol 需要指定 portfolio.vol_factor")
        return self


class VolTargetConfig(BaseModel):
    """事前波动率目标：按近 window 日收益协方差估算目标组合的年化波动，超过目标时
    等比降低总仓位（只降不加杠杆）。

    Attributes:
        target: 目标年化波动率（小数，如 0.15）。
        window: 协方差窗口（交易日）。
        floor: 缩放后总仓位下限占原仓位的比例（0 表示可降到空仓）。
    """

    target: float = Field(gt=0.0, le=1.0)
    window: int = Field(default=60, ge=20, le=250)
    floor: float = Field(default=0.0, ge=0.0, le=1.0)


class DrawdownStopConfig(BaseModel):
    """组合回撤止损：策略自身净值从峰值回撤达到阈值后，把总仓位压到 exposure，
    冷却 cooldown_days 个决策日后恢复并以当时净值重置峰值。

    Attributes:
        threshold: 触发回撤（小数，如 0.15 表示回撤 15%）。
        exposure: 止损期间的总仓位上限（0-1）。
        cooldown_days: 冷却决策日数。
    """

    threshold: float = Field(gt=0.0, lt=1.0)
    exposure: float = Field(default=0.0, ge=0.0, le=1.0)
    cooldown_days: int = Field(default=20, ge=1, le=250)


class TrailingStopConfig(BaseModel):
    """单资产移动止损：持仓资产自入场以来收盘价从最高点回撤达到阈值即清出，冷却期内
    不再入选。

    Attributes:
        threshold: 触发回撤（小数）。
        cooldown_days: 冷却决策日数。
    """

    threshold: float = Field(gt=0.0, lt=1.0)
    cooldown_days: int = Field(default=20, ge=1, le=250)


class RiskConfig(BaseModel):
    """风控配置（可选模块）。

    前三项为静态约束（逐日裁剪目标权重）；``vol_target`` / ``drawdown_stop`` /
    ``trailing_stop`` 为路径依赖叠加层，在回测主循环合成当日目标后施加（实时决策通过
    回放重建状态），缺省 None 表示不启用，其余口径不变。

    Attributes:
        max_asset_weight: 单资产仓位上限。
        max_portfolio_exposure: 组合总仓位上限。
        min_cash_ratio: 最低现金比例。
        vol_target: 事前波动率目标。
        drawdown_stop: 组合回撤止损。
        trailing_stop: 单资产移动止损。
    """

    max_asset_weight: float = Field(default=0.30, gt=0.0, le=1.0)
    max_portfolio_exposure: float = Field(default=1.0, ge=0.0, le=1.0)
    min_cash_ratio: float = Field(default=0.0, ge=0.0, lt=1.0)
    vol_target: VolTargetConfig | None = None
    drawdown_stop: DrawdownStopConfig | None = None
    trailing_stop: TrailingStopConfig | None = None

    @property
    def has_overlay(self) -> bool:
        """是否启用了任一路径依赖叠加层。"""
        return any((self.vol_target, self.drawdown_stop, self.trailing_stop))


class RebalanceScheduleConfig(BaseModel):
    """单个调仓腿的频率配置。

    严格校验（避免静默失效）：
    - ``frequency`` 只接受 daily / weekly / biweekly / monthly——未知频率若放行会在
      调度器末尾 ``return True``，静默退化为每日调仓（换手与成本最大）；
    - ``day_of_week`` 0-4（周一至周五）；``day_of_month`` 1-31，超出当月天数时按
      当月最后一日处理（不会整月不调仓）；
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


class RebalanceBandConfig(BaseModel):
    """偏离阈值调仓。

    Attributes:
        min_turnover: 日历调仓日所需单边换手低于该值时跳过本次调仓（小数，如 0.05）。
        max_drift: 非调仓日实际权重相对最近一次目标的最大单资产偏离超过该值时，按最近
            目标再平衡（不重新选股；小数，如 0.05）。
    """

    min_turnover: float | None = Field(default=None, gt=0.0, le=1.0)
    max_drift: float | None = Field(default=None, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _require_any(self) -> "RebalanceBandConfig":
        """至少启用一项阈值。

        Returns:
            校验通过的原对象。

        Raises:
            ValueError: 两项都为空时抛出。
        """
        if self.min_turnover is None and self.max_drift is None:
            raise ValueError("rebalance.band 至少需要 min_turnover 或 max_drift 之一")
        return self


class RebalanceConfig(BaseModel):
    """双腿调仓配置（可选模块）。

    启用本模块时**两条腿必选**（都有默认值，因此 JSON 中可只给一条腿，另一条腿
    按"与已给出的那条一致"归一化）：

    - ``selection`` 选股腿：重建组合成分与权重；
    - ``risk`` 风险腿：只把**现有成分**等比缩放到择时目标总仓位，不引入新成分。

    两腿频率一致时（平铺写法的存量配置升级后即如此）等价于单腿口径：每个调仓日都按
    "新成分 + 择时目标仓位"重建；只有两腿频率不同时才会出现"仅换成分保持仓位"
    或"仅缩放仓位"的差异化日程。

    旧平铺配置（``frequency`` / ``day_of_week`` / ``day_of_month`` 直接挂在
    ``rebalance`` 下）会被升级为**两腿同频**，从而保持历史回测口径不变。
    """

    selection: RebalanceScheduleConfig = Field(default_factory=RebalanceScheduleConfig)
    risk: RebalanceScheduleConfig = Field(default_factory=RebalanceScheduleConfig)
    band: RebalanceBandConfig | None = None

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
            legacy_keys = ("frequency", "day_of_week", "week_parity", "day_of_month")
            legacy = {key: value[key] for key in legacy_keys if key in value}
            # 平铺字段之外的键（如偏离阈值 band）原样保留，不随升级丢失
            rest = {key: item for key, item in value.items() if key not in legacy_keys}
            return {**rest, "selection": legacy, "risk": dict(legacy)}
        normalized = dict(value)
        # 双腿必选：只给出其中一条腿时，另一条按相同日程补齐（不改变单腿语义）
        if "selection" not in normalized and "risk" in normalized:
            normalized["selection"] = normalized["risk"]
        if "risk" not in normalized and "selection" in normalized:
            normalized["risk"] = normalized["selection"]
        return normalized

    # 兼容平铺写法的调用方与外部代码；新代码使用 selection / risk。
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
        portfolio: 组合配置；缺失时自动补为默认等权配置（兼容存量配置）。
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

    def _all_ranks(self) -> list[RankConfig]:
        """默认排名配置与各 regime 覆盖的排名配置。"""
        return [self.rank] + [r.rank for r in self.regime_rules.values() if r.rank]

    def _all_portfolios(self) -> list[PortfolioConfig]:
        """默认组合配置与各 regime 覆盖的组合配置。"""
        return [self.portfolio] + [r.portfolio for r in self.regime_rules.values() if r.portfolio]

    @property
    def needs_path_state(self) -> bool:
        """决策是否依赖持仓或净值路径（排名缓冲、偏离调仓、风控叠加层）。

        为真时实时决策不能只看当日截面，必须回放一段历史重建持仓与风控状态。
        """
        if any(rank.buffer_n for rank in self._all_ranks()):
            return True
        if self.rebalance is not None and self.rebalance.band is not None:
            return True
        return self.risk is not None and self.risk.has_overlay

    @property
    def return_window(self) -> int | None:
        """需要的逐日收益历史窗口（交易日）：风险平价与波动率目标共用。

        Returns:
            最大窗口；都未启用时为 None（不装配收益历史）。
        """
        windows = [p.cov_window for p in self._all_portfolios() if p.method == "risk_parity"]
        if self.risk is not None and self.risk.vol_target is not None:
            windows.append(self.risk.vol_target.window)
        return max(windows) if windows else None


def iter_factor_refs(config: StrategyConfig) -> list[tuple[str, str, str]]:
    """列出策略各模块引用的全部因子（不去重），作为引用收集的唯一实现。

    因子加载、配置校验（引用存在性与 usage 匹配）、未使用别名提示都依赖同一份
    引用清单；集中在这里实现，避免各处各写一遍遍历、新增模块时漏掉某一处。

    Args:
        config: 已解析的策略配置。

    Returns:
        ``(模块 key, 中文位置标签, 因子引用)`` 列表；模块 key 取 score / timing /
        filter / rank / portfolio，前四者与因子模板 ``usage`` 取值一致；portfolio
        （inverse_vol 的波动率因子）按资产级取值，用途校验按 score 口径。
    """
    refs: list[tuple[str, str, str]] = []
    refs.extend(("score", "评分", ref) for ref in config.score.factors)
    if config.timing:
        refs.extend(("timing", "择时", ref) for ref in config.timing.market_refs())
    if config.filters:
        refs.extend(("filter", "过滤", ref) for ref in filter_rule_refs(config.filters))
    for ref in (config.rank.momentum_factor, config.rank.valuation_factor):
        if ref:
            refs.append(("rank", "排名子因子", ref))
    if config.portfolio.vol_factor:
        refs.append(("portfolio", "组合波动率因子", config.portfolio.vol_factor))
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
        if regime_rule.portfolio and regime_rule.portfolio.vol_factor:
            refs.append(("portfolio", "regime 组合波动率因子", regime_rule.portfolio.vol_factor))
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
    for group in filters.groups:
        refs.extend(filter_rule_refs(group))
    return refs


# ── 组合策略（多策略持仓级合成） ──────────────────────────────────────────

COMPOSITE_KIND = "composite"


class CompositeMemberConfig(BaseModel):
    """组合策略的成员。

    Attributes:
        strategy_id: 成员策略 ID（必须是普通策略，不能是组合策略）。
        weight: 目标权重（0-1）。
    """

    strategy_id: str = Field(min_length=1)
    weight: float = Field(gt=0.0, le=1.0)


class CompositeStrategyConfig(BaseModel):
    """组合策略：按目标权重合成多个普通策略的持仓后统一记账。

    每个成员保留自己的引擎状态与子账本（择时、风控、调仓日程互不影响）；组合账本
    = Σ 成员权重 × 成员持仓，成员权重随各自净值漂移，在 ``rebalance`` 日程上调回
    目标权重。换手按合成后的持仓计，成员之间方向相反的交易被轧差。

    Attributes:
        strategy_id: 组合策略 ID。
        display_name: 展示名。
        version: 版本号。
        schema_version: 配置 schema 版本。
        description: 描述。
        frequency: 标注频率（元数据）。
        kind: 固定为 ``composite``。
        members: 成员列表（策略 ID 不重复）。
        cash_weight: 现金目标权重（收益为 0）。
        rebalance: 成员权重再平衡日程，缺省每日（恒定比例）。
    """

    strategy_id: str
    display_name: str
    version: str = "1.0.0"
    schema_version: str = "2"
    description: str = ""
    frequency: str = "daily"
    kind: Literal["composite"] = COMPOSITE_KIND
    members: list[CompositeMemberConfig] = Field(min_length=1, max_length=10)
    cash_weight: float = Field(default=0.0, ge=0.0, lt=1.0)
    rebalance: RebalanceScheduleConfig = Field(default_factory=RebalanceScheduleConfig)

    @model_validator(mode="after")
    def _check_members(self) -> "CompositeStrategyConfig":
        """成员不重复、不含自身，且成员权重 + 现金权重 = 1。

        Returns:
            校验通过的原对象。

        Raises:
            ValueError: 规则不满足时抛出。
        """
        ids = [member.strategy_id for member in self.members]
        if len(set(ids)) != len(ids):
            raise ValueError("组合策略成员不能重复")
        if self.strategy_id in ids:
            raise ValueError("组合策略不能包含自身")
        total = sum(member.weight for member in self.members) + self.cash_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"成员权重与现金权重之和必须为 1（当前 {total:.4f}）")
        return self

    @property
    def target_weights(self) -> dict[str, float]:
        """成员 ID → 目标权重。"""
        return {member.strategy_id: member.weight for member in self.members}


def is_composite_config(config_json: Any) -> bool:
    """判断 config_json 是否为组合策略配置。

    Args:
        config_json: 策略 config_json。

    Returns:
        ``kind == "composite"`` 时为 True。
    """
    return isinstance(config_json, dict) and config_json.get("kind") == COMPOSITE_KIND
