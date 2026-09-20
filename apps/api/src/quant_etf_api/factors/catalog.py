"""因子模板注册表与实例解析。

注册表集中登记全部内置模板（``build_default_templates``），并负责把策略配置里的
因子引用解析成可执行实例：

1. 引用名出现在策略的 ``factor_aliases`` 中 → 使用其中声明的模板与参数；
2. 否则引用名本身是已注册模板 ID → 使用该模板的默认参数；
3. 否则解析失败，配置校验期即快速失败。

解析结果 ``FactorInstance`` 是「一次具体计算」的身份：实例 ID、模板、规范化参数
与计算器。引擎层与因子查询层以实例 ID 作为 ``asset_factors`` 的键，因此计算语义
完全由模板与参数决定，与实例叫什么名字无关。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from quant_etf_api.factors.base import FactorComputer, period_lookback_days
from quant_etf_api.factors.templates import FactorTemplate, ParameterSpec, build_template

# ── 可调参模板的参数声明 ────────────────────────────────────────────────
# 单周期窗口统一走「交易日周期」口径，交易日 → 自然日的折算系数见
# base.period_lookback_days。
_SMA_PERIOD = ParameterSpec("integer", 2, 250, 20, "均线周期（交易日）")
_RETURN_PERIOD = ParameterSpec("integer", 1, 250, 20, "收益回望交易日数")
_RETURN_STD_PERIOD = ParameterSpec("integer", 2, 250, 20, "收益率标准差窗口（交易日）")
_RSI_PERIOD = ParameterSpec("integer", 2, 100, 14, "RSI 周期（交易日）")
_ATR_PERIOD = ParameterSpec("integer", 2, 100, 14, "ATR 周期（交易日）")
_VOLATILITY_PERIOD = ParameterSpec("integer", 2, 250, 20, "年化波动率窗口（交易日）")
_VOLUME_RATIO_PERIOD = ParameterSpec("integer", 2, 250, 20, "成交量量比窗口（交易日）")
_AMOUNT_RATIO_PERIOD = ParameterSpec("integer", 2, 250, 20, "成交额量比窗口（交易日）")
_HIGH_LOW_PERIOD = ParameterSpec("integer", 2, 250, 63, "区间宽度窗口（交易日）")
_DONCHIAN_PERIOD = ParameterSpec("integer", 2, 250, 20, "通道回望周期（交易日）")
_DRAWDOWN_PERIOD = ParameterSpec("integer", 2, 250, 60, "回撤回望窗口（交易日）")
_MA_DEVIATION_PERIOD = ParameterSpec("integer", 2, 250, 60, "乖离率均线周期（交易日）")
_POSITION_IR_PERIOD = ParameterSpec(
    "integer", 25, 250, 60, "日内位置窗口（交易日，须大于有效样本门槛 20）"
)
_DAYS_BEYOND_PERIOD = ParameterSpec("integer", 2, 250, 21, "均值±标准差统计窗口（交易日）")
_BREADTH_PERIOD = ParameterSpec("integer", 2, 250, 20, "市场宽度均线周期（交易日）")
_SHARPE_PERIOD = ParameterSpec("integer", 2, 250, 60, "收益回望窗口（交易日）")
_SHARPE_VOL_PERIOD = ParameterSpec("integer", 2, 250, 20, "年化波动率窗口（交易日）")
_LOW_AMPLITUDE_PERIOD = ParameterSpec("integer", 2, 250, 160, "振幅排序窗口（交易日）")
_LOW_AMPLITUDE_RATIO = ParameterSpec("number", 0.05, 1.0, 0.70, "保留的低振幅交易日占比")
_RSRS_N = ParameterSpec("integer", 5, 40, 18, "高低价 OLS 回归窗口（交易日）")
_RSRS_M = ParameterSpec("integer", 20, 400, 250, "修正斜率标准化窗口（交易日）")
_MONTHLY_MA_MONTHS = ParameterSpec("integer", 2, 12, 10, "月线均线周期（月）")
_MONTHLY_RETURN_MONTHS = ParameterSpec("integer", 1, 12, 2, "月线动量回望月数")
_PMI_MONTHS = ParameterSpec("integer", 1, 12, 3, "PMI 动量回看月数")


class FactorResolutionError(ValueError):
    """因子引用无法解析为模板实例。"""


@runtime_checkable
class FactorAliasLike(Protocol):
    """策略配置中单别名声明的结构契约（template_id + params）。"""

    @property
    def template_id(self) -> str:
        """目标模板 ID。"""
        ...

    @property
    def params(self) -> Mapping[str, Any]:
        """参数覆盖。"""
        ...


@dataclass(frozen=True, eq=False)
class FactorInstance:
    """一次具体因子计算的执行身份。

    Attributes:
        instance_id: 实例标识，等于策略中的引用名（别名或模板 ID）。
        label: 展示名，如「20日均线」。
        template: 所属模板。
        params: 已规范化的完整参数字典。
        computer: 按参数创建的计算器。
    """

    instance_id: str
    label: str
    template: FactorTemplate
    params: Mapping[str, Any]
    computer: FactorComputer

    @property
    def template_id(self) -> str:
        """所属模板 ID。"""
        return self.template.template_id

    @property
    def dedup_key(self) -> tuple[str, str]:
        """同参去重键：模板 ID + 规范化参数文本。"""
        return (self.template.template_id, self.template.normalize_text(self.params))

    @property
    def lookback_days(self) -> int:
        """该实例所需的回望自然日数。"""
        return self.template.lookback_days(self.params)

    def to_payload(self) -> dict[str, Any]:
        """转换为接口/报告展示字典。

        Returns:
            含 template_id / template_version / params 的字典。
        """
        return {
            "template_id": self.template.template_id,
            "template_version": self.template.version,
            "params": dict(self.params),
        }


class FactorTemplateRegistry:
    """因子模板注册表，以 template_id 为 key。"""

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._templates: dict[str, FactorTemplate] = {}

    def register(self, template: FactorTemplate) -> None:
        """登记一个模板。

        Args:
            template: 待登记的因子模板。

        Raises:
            ValueError: template_id 重复。
        """
        if template.template_id in self._templates:
            raise ValueError(f"因子模板 {template.template_id} 已注册，请勿重复注册")
        self._templates[template.template_id] = template

    def all(self) -> list[FactorTemplate]:
        """返回全部已登记模板（按 template_id 升序）。"""
        return [self._templates[key] for key in sorted(self._templates)]

    def get(self, template_id: str) -> FactorTemplate | None:
        """按 template_id 查找模板，未找到返回 None。

        Args:
            template_id: 模板标识。
        """
        return self._templates.get(template_id)

    def ids(self) -> list[str]:
        """返回全部模板 ID（升序）。"""
        return sorted(self._templates)

    def resolve(
        self,
        ref: str,
        factor_aliases: Mapping[str, FactorAliasLike] | None = None,
    ) -> FactorInstance:
        """把策略中的因子引用解析成可执行实例。

        Args:
            ref: 因子引用名（策略别名或模板 ID）。
            factor_aliases: 策略声明的别名声明的映射，None 表示无别名声明。

        Returns:
            FactorInstance。

        Raises:
            FactorResolutionError: 引用了未声明的别名、未知模板，或参数不合法。
        """
        alias = (factor_aliases or {}).get(ref)
        if alias is not None:
            template = self._templates.get(alias.template_id)
            if template is None:
                raise FactorResolutionError(
                    f"别名 {ref} 指向未注册的模板 {alias.template_id}，可用模板：{self.ids()}"
                )
            params: Mapping[str, Any] = dict(alias.params or {})
        else:
            template = self._templates.get(ref)
            if template is None:
                raise FactorResolutionError(
                    f"未知因子 {ref}：既未在 factor_aliases 中声明，也不是已注册模板"
                    f"（可用模板：{self.ids()}）"
                )
            params = {}

        try:
            resolved = template.resolve_params(params)
        except ValueError as exc:
            raise FactorResolutionError(f"因子 {ref} 参数不合法：{exc}") from exc

        return FactorInstance(
            instance_id=ref,
            label=template.label(resolved),
            template=template,
            params=resolved,
            computer=template.build_computer(resolved),
        )

    def resolve_params(
        self,
        template_id: str,
        params: Mapping[str, Any] | None = None,
    ) -> FactorInstance:
        """按显式模板 ID 与参数解析实例，供因子查询接口直接使用。

        Args:
            template_id: 因子模板 ID。
            params: 参数覆盖，None 表示默认口径。

        Returns:
            FactorInstance，实例 ID 等于模板 ID。

        Raises:
            FactorResolutionError: 模板未注册。
            FactorParameterError: 参数不合法。
        """
        template = self._templates.get(template_id)
        if template is None:
            raise FactorResolutionError(f"未知因子模板 {template_id}：可用模板 {self.ids()}")
        resolved = template.resolve_params(params)
        return FactorInstance(
            instance_id=template_id,
            label=template.label(resolved),
            template=template,
            params=resolved,
            computer=template.build_computer(resolved),
        )

    def resolve_all(
        self,
        refs: Iterable[str],
        factor_aliases: Mapping[str, FactorAliasLike] | None = None,
    ) -> dict[str, FactorInstance]:
        """批量解析因子引用。

        Args:
            refs: 因子引用名集合（可重复）。
            factor_aliases: 策略别名声明映射。

        Returns:
            {引用名: FactorInstance}；解析失败的引用直接抛错。

        Raises:
            FactorResolutionError: 任一引用无法解析。
        """
        resolved: dict[str, FactorInstance] = {}
        for ref in refs:
            if ref not in resolved:
                resolved[ref] = self.resolve(ref, factor_aliases)
        return resolved

    @staticmethod
    def max_lookback_days(instances: Iterable[FactorInstance]) -> int:
        """返回本次计算所需的最大回望自然日数。

        Args:
            instances: 本次实际计算的因子实例。

        Returns:
            最大回望自然日数；无实例时返回 90 作为兜底。
        """
        return max((instance.lookback_days for instance in instances), default=90)


def _period_lookback(params: Mapping[str, Any]) -> int:
    """按周期参数推导回望自然日数（技术指标通用口径）。

    Args:
        params: 已规范化参数，需含 period。

    Returns:
        回望自然日数。
    """
    return period_lookback_days(int(params["period"]))


def _max_period_lookback(params: Mapping[str, Any]) -> int:
    """按多个周期参数中的最大值推导回望自然日数。

    Args:
        params: 已规范化参数，所有值均为交易日周期。

    Returns:
        回望自然日数，按最大窗口推导。
    """
    return period_lookback_days(max(int(value) for value in params.values()))


def _period_label(suffix: str) -> Callable[[Mapping[str, Any]], str]:
    """构造「N + 后缀」形式的实例展示名生成函数。

    Args:
        suffix: 中文后缀，如「日均线」。

    Returns:
        接收规范化参数、返回展示名的函数。
    """

    def _label(params: Mapping[str, Any]) -> str:
        return f"{params['period']}{suffix}"

    return _label


def _low_amplitude_label(params: Mapping[str, Any]) -> str:
    """构造低振幅动量的实例展示名。

    Args:
        params: 已规范化参数（period + low_amplitude_ratio）。

    Returns:
        形如「160日低振幅动量（70%）」的展示名。
    """
    return (
        f"{params['period']}日低振幅动量"
        f"（{int(float(params['low_amplitude_ratio']) * 100)}%）"
    )


def _sharpe_label(params: Mapping[str, Any]) -> str:
    """构造风险调整动量的实例展示名。

    Args:
        params: 已规范化参数（period + volatility_period）。

    Returns:
        形如「60日风险调整动量（波动率20日）」的展示名。
    """
    return f"{params['period']}日风险调整动量（波动率{params['volatility_period']}日）"


def _rsrs_label(params: Mapping[str, Any]) -> str:
    """构造 RSRS 的实例展示名。

    Args:
        params: 已规范化参数（n + m）。

    Returns:
        形如「RSRS阻力支撑相对强度（18/250）」的展示名。
    """
    return f"RSRS阻力支撑相对强度（{params['n']}/{params['m']}）"


def _months_label(suffix: str) -> Callable[[Mapping[str, Any]], str]:
    """构造「N + 月后缀」形式的实例展示名生成函数。

    Args:
        suffix: 中文后缀，如「月均线」。

    Returns:
        接收规范化参数、返回展示名的函数。
    """

    def _label(params: Mapping[str, Any]) -> str:
        return f"{params['months']}{suffix}"

    return _label


def build_default_templates() -> list[FactorTemplate]:
    """构建全部内置因子模板。

    模板目录按「同一计算逻辑只登记一个模板」组织：周期、窗口、比例等可调
    数值一律声明为 ``parameter_schema``，不再为每个固定取值单独注册模板；
    计算逻辑确实不含可调数值的模板为零参数模板，元数据与固定口径取自计算器
    自述的 FactorSpec，同一份元数据不在两处维护。

    Returns:
        因子模板列表。
    """
    from quant_etf_api.factors.builtins.breadth import BreadthMAComputer
    from quant_etf_api.factors.builtins.erp import ERPComputer, ERPPercentileComputer
    from quant_etf_api.factors.builtins.index_panel_factors import (
        IndexDiffusionRatioComputer,
        RRGIndustryMatchComputer,
    )
    from quant_etf_api.factors.builtins.macro import PMIMomentumComputer, pmi_lookback_days
    from quant_etf_api.factors.builtins.monthly import (
        MonthlyMAComputer,
        MonthlyReturnComputer,
        MonthlyStreakComputer,
        monthly_lookback_days,
    )
    from quant_etf_api.factors.builtins.momentum import (
        DaysDownUpComputer,
        LowAmplitudeMomentumComputer,
        PricePositionIrComputer,
        ReturnComputer,
        RsrsComputer,
        SharpeComputer,
        low_amplitude_lookback_days,
        rsrs_lookback_days,
    )
    from quant_etf_api.factors.builtins.price import ChangePctComputer, ClosePriceComputer
    from quant_etf_api.factors.builtins.technical import (
        ATRComputer,
        DaysBeyondUpperLowerComputer,
        DonchianHighComputer,
        DonchianLowComputer,
        DrawdownComputer,
        MADeviationComputer,
        MAComputer,
        RSIComputer,
    )
    from quant_etf_api.factors.builtins.valuation import (
        PBPercentileComputer,
        PEPercentileComputer,
    )
    from quant_etf_api.factors.builtins.volatility import (
        HighLowRangeComputer,
        ReturnStdComputer,
        VolatilityComputer,
    )
    from quant_etf_api.factors.builtins.volume import AmountRatioComputer, VolumeRatioComputer

    templates: list[FactorTemplate] = []

    # ── 可调参模板 ──────────────────────────────────────────────────────
    tunable: list[FactorTemplate] = [
        build_template(
            "sma",
            lambda p: MAComputer(period=int(p["period"])),
            parameter_schema={"period": _SMA_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日均线"),
            name="简单移动平均线",
            description="收盘价的 N 日简单移动平均，用于趋势判定与均线比较。",
            category="technical",
        ),
        build_template(
            "return",
            lambda p: ReturnComputer(period=int(p["period"])),
            parameter_schema={"period": _RETURN_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日收益率"),
            name="N 日累计收益率",
            description="指数近 N 个交易日的收盘价涨跌幅（%），衡量动量。",
            category="momentum",
        ),
        build_template(
            "return_std",
            lambda p: ReturnStdComputer(period=int(p["period"])),
            parameter_schema={"period": _RETURN_STD_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日收益率标准差"),
            name="N 日收益率标准差",
            description="近 N 个交易日日收益率的标准差（不年化），衡量波动水平。",
            category="volatility",
        ),
        build_template(
            "rsi",
            lambda p: RSIComputer(period=int(p["period"])),
            parameter_schema={"period": _RSI_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日RSI"),
            name="相对强弱指标",
            description="近 N 日上涨与下跌幅度之比映射到 0-100 的相对强弱指标。",
            category="technical",
        ),
        build_template(
            "atr",
            lambda p: ATRComputer(period=int(p["period"])),
            parameter_schema={"period": _ATR_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日ATR"),
            name="平均真实波幅",
            description="近 N 日真实波幅均值，衡量价格波动幅度。",
            category="technical",
        ),
        build_template(
            "volatility",
            lambda p: VolatilityComputer(period=int(p["period"])),
            parameter_schema={"period": _VOLATILITY_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日年化波动率"),
            name="N 日年化波动率",
            description=(
                "近 N 个交易日日收益率的年化标准差（%），"
                "公式 std(N 个日收益率, ddof=1) × sqrt(252) × 100。"
            ),
            category="volatility",
        ),
        build_template(
            "volume_ratio",
            lambda p: VolumeRatioComputer(period=int(p["period"])),
            parameter_schema={"period": _VOLUME_RATIO_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日量比"),
            name="N 日量比",
            description="当日成交量与近 N 个交易日平均成交量的比值，量比>1 表示相对放量。",
            category="volume",
        ),
        build_template(
            "amount_ratio",
            lambda p: AmountRatioComputer(period=int(p["period"])),
            parameter_schema={"period": _AMOUNT_RATIO_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日成交额量比"),
            name="N 日成交额量比",
            description=(
                "当日成交额与近 N 个交易日平均成交额的比值，"
                "量比>1 表示相对放量，反映资金参与度变化。"
            ),
            category="volume",
        ),
        build_template(
            "high_low",
            lambda p: HighLowRangeComputer(period=int(p["period"])),
            parameter_schema={"period": _HIGH_LOW_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日区间宽度"),
            name="N 日区间宽度",
            description=(
                "近 N 个交易日最高收盘与最低收盘之间的区间宽度（%），"
                "公式 (最高收盘 − 最低收盘) / 最低收盘 × 100。"
            ),
            category="volatility",
        ),
        build_template(
            "donchian_high",
            lambda p: DonchianHighComputer(period=int(p["period"])),
            parameter_schema={"period": _DONCHIAN_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日通道上轨"),
            name="Donchian 通道上轨",
            description="近 N 个交易日的最高价，用于突破策略。",
            category="technical",
        ),
        build_template(
            "donchian_low",
            lambda p: DonchianLowComputer(period=int(p["period"])),
            parameter_schema={"period": _DONCHIAN_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日通道下轨"),
            name="Donchian 通道下轨",
            description="近 N 个交易日的最低价，用于止损策略。",
            category="technical",
        ),
        build_template(
            "drawdown",
            lambda p: DrawdownComputer(period=int(p["period"])),
            parameter_schema={"period": _DRAWDOWN_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日回撤幅度"),
            name="N 日回撤幅度",
            description=(
                "当前收盘价相对近 N 个交易日最高收盘价的回撤幅度（%），"
                "返回负数或零；payload 附自峰值以来的水下交易日数。"
            ),
            category="technical",
        ),
        build_template(
            "ma_deviation",
            lambda p: MADeviationComputer(period=int(p["period"])),
            parameter_schema={"period": _MA_DEVIATION_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日均线乖离率"),
            name="N 日均线乖离率",
            description=(
                "当前收盘价相对 N 日均线的偏离百分比（%），正值表示价格位于均线上方。"
            ),
            category="technical",
        ),
        build_template(
            "price_position_ir",
            lambda p: PricePositionIrComputer(period=int(p["period"])),
            parameter_schema={"period": _POSITION_IR_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日日内位置信息比率"),
            name="N 日日内位置信息比率",
            description=(
                "近 N 个交易日日内位置比率 (close − open) / (high − low) 的"
                "均值与标准差之比（信息比率）。"
            ),
            category="momentum",
        ),
        build_template(
            "days_beyond_upper_lower",
            lambda p: DaysBeyondUpperLowerComputer(period=int(p["period"])),
            parameter_schema={"period": _DAYS_BEYOND_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日超越均值±标准差天数差"),
            name="N 日超越均值±标准差天数差",
            description=(
                "近 N 个交易日收盘价高于 mean+std 的天数减去低于 mean−std 的天数"
                "（mean/std 取同一窗口，ddof=1）。"
            ),
            category="technical",
        ),
        build_template(
            "breadth_ma_pct",
            lambda p: BreadthMAComputer(period=int(p["period"])),
            parameter_schema={"period": _BREADTH_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=lambda p: f"MA{p['period']}市场宽度",
            name="市场宽度",
            description=(
                "全市场活跃指数中收盘价站上自身 N 日均线的占比（0-100），"
                "衡量上升趋势扩散程度。市场级因子，适合择时或过滤。"
            ),
            category="technical",
        ),
        build_template(
            "sharpe",
            lambda p: SharpeComputer(
                period=int(p["period"]), volatility_period=int(p["volatility_period"])
            ),
            parameter_schema={
                "period": _SHARPE_PERIOD,
                "volatility_period": _SHARPE_VOL_PERIOD,
            },
            compute_lookback_days=_max_period_lookback,
            instance_label=_sharpe_label,
            name="风险调整动量",
            description=(
                "收益窗口涨跌幅(%) / 波动率窗口年化波动率(%)，"
                "衡量每单位波动获得的收益，值越高表示动量越稳健。"
            ),
            category="momentum",
        ),
        build_template(
            "low_amplitude_momentum",
            lambda p: LowAmplitudeMomentumComputer(
                period=int(p["period"]),
                low_amplitude_ratio=float(p["low_amplitude_ratio"]),
            ),
            parameter_schema={
                "period": _LOW_AMPLITUDE_PERIOD,
                "low_amplitude_ratio": _LOW_AMPLITUDE_RATIO,
            },
            compute_lookback_days=lambda p: low_amplitude_lookback_days(int(p["period"])),
            instance_label=_low_amplitude_label,
            name="低振幅条件动量",
            description=(
                "最近 N 个交易日按日振幅(high/low−1)从低到高排序，"
                "累加最低振幅 λ 比例交易日的收盘价日收益率（%）。"
            ),
            category="momentum",
        ),
        build_template(
            "rsrs",
            lambda p: RsrsComputer(n=int(p["n"]), m=int(p["m"])),
            parameter_schema={"n": _RSRS_N, "m": _RSRS_M},
            compute_lookback_days=lambda p: rsrs_lookback_days(int(p["n"]), int(p["m"])),
            instance_label=_rsrs_label,
            name="RSRS阻力支撑相对强度",
            description=(
                "近 N 个交易日最高价对最低价做 OLS 回归，取斜率 β × R² 为修正值，"
                "再对最近 M 个交易日的修正值序列做 z-score 标准化，输出标准分。"
            ),
            category="momentum",
        ),
        build_template(
            "monthly_ma",
            lambda p: MonthlyMAComputer(months=int(p["months"])),
            parameter_schema={"months": _MONTHLY_MA_MONTHS},
            compute_lookback_days=lambda p: monthly_lookback_days(int(p["months"])),
            instance_label=_months_label("月均线"),
            name="月线均线",
            description="指数近 N 个月线收盘价的简单移动平均，从日线实时聚合。",
            category="technical",
        ),
        build_template(
            "monthly_return",
            lambda p: MonthlyReturnComputer(months=int(p["months"])),
            parameter_schema={"months": _MONTHLY_RETURN_MONTHS},
            compute_lookback_days=lambda p: monthly_lookback_days(int(p["months"])),
            instance_label=_months_label("月收益率"),
            name="月线动量",
            description="指数近 N 个月的收益率（%），基于月线收盘价。",
            category="momentum",
        ),
        build_template(
            "pmi_momentum",
            lambda p: PMIMomentumComputer(months=int(p["months"])),
            parameter_schema={"months": _PMI_MONTHS},
            compute_lookback_days=lambda p: pmi_lookback_days(int(p["months"])),
            instance_label=_months_label("个月PMI动量"),
            name="PMI 动量",
            description=(
                "最新制造业 PMI 与约 N 个月前 PMI 的差值，衡量经济扩张/收缩趋势变化。"
            ),
            category="macro",
        ),
    ]
    templates.extend(tunable)

    # ── 零参数模板 ──────────────────────────────────────────────────────
    zero_param: list[tuple[str, Any]] = [
        ("close_price", lambda _p: ClosePriceComputer()),
        ("change_pct", lambda _p: ChangePctComputer()),
        ("days_down_up", lambda _p: DaysDownUpComputer()),
        ("monthly_up_streak", lambda _p: MonthlyStreakComputer()),
        ("pe_percentile", lambda _p: PEPercentileComputer()),
        ("pb_percentile", lambda _p: PBPercentileComputer()),
        ("erp", lambda _p: ERPComputer()),
        ("erp_percentile", lambda _p: ERPPercentileComputer()),
        ("index_diffusion_ratio", lambda _p: IndexDiffusionRatioComputer()),
        ("rrg_industry_match_score", lambda _p: RRGIndustryMatchComputer()),
    ]
    for template_id, factory in zero_param:
        templates.append(build_template(template_id, factory))

    return templates


def build_default_registry() -> FactorTemplateRegistry:
    """构建包含全部内置模板的注册表。

    Returns:
        已登记全部内置模板的 FactorTemplateRegistry。
    """
    registry = FactorTemplateRegistry()
    for template in build_default_templates():
        registry.register(template)
    return registry


_default_registry: FactorTemplateRegistry | None = None


def get_factor_template_registry() -> FactorTemplateRegistry:
    """返回进程级单例模板注册表，首次调用时构建，后续复用。"""
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry
