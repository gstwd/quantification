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

from quant_etf_api.factors.base import FactorComputer
from quant_etf_api.factors.templates import FactorTemplate, ParameterSpec, build_template

# 首批可配模板的周期参数取值范围（依方案 §4.2 的参数定义表）
_SMA_PERIOD = ParameterSpec("integer", 2, 250, 20, "均线周期（交易日）")
_RETURN_PERIOD = ParameterSpec("integer", 1, 250, 20, "收益回望交易日数")
_RETURN_STD_PERIOD = ParameterSpec("integer", 2, 250, 20, "收益率标准差窗口（交易日）")
_RSI_PERIOD = ParameterSpec("integer", 2, 100, 14, "RSI 周期（交易日）")
_ATR_PERIOD = ParameterSpec("integer", 2, 100, 14, "ATR 周期（交易日）")


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
        回望自然日数，至少 15 天。
    """
    return max(15, int(params["period"] * 1.5) + 5)


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


def build_default_templates() -> list[FactorTemplate]:
    """构建全部内置因子模板。

    首批可配模板（sma/return/return_std/rsi/atr）声明周期参数的取值范围与
    回望窗口推导；其余模板为零参数模板，元数据与固定口径全部取自计算器自述的
    FactorSpec，同一份元数据不在两处维护。

    Returns:
        因子模板列表。
    """
    from quant_etf_api.factors.builtins.breadth import BreadthMA20Computer
    from quant_etf_api.factors.builtins.erp import ERPComputer, ERPPercentileComputer
    from quant_etf_api.factors.builtins.index_panel_factors import (
        IndexDiffusionRatioComputer,
        RRGIndustryMatchComputer,
    )
    from quant_etf_api.factors.builtins.macro import PMIMomentumComputer
    from quant_etf_api.factors.builtins.monthly import (
        MonthlyMAComputer,
        MonthlyReturnComputer,
        MonthlyStreakComputer,
    )
    from quant_etf_api.factors.builtins.momentum import (
        DaysDownUpComputer,
        LowAmplitudeMomentumComputer,
        PricePositionIrComputer,
        ReturnComputer,
        RsrsComputer,
        Sharpe60dComputer,
    )
    from quant_etf_api.factors.builtins.price import ChangePctComputer, ClosePriceComputer
    from quant_etf_api.factors.builtins.technical import (
        ATRComputer,
        DaysBeyondUpperLowerComputer,
        DonchianHighComputer,
        DonchianLowComputer,
        DrawdownCurrentComputer,
        MADeviationComputer,
        MAComputer,
        MaxDrawdown60dComputer,
        RSIComputer,
    )
    from quant_etf_api.factors.builtins.valuation import (
        PBPercentileComputer,
        PEPercentileComputer,
    )
    from quant_etf_api.factors.builtins.volatility import (
        HighLowRangeComputer,
        ReturnStdComputer,
        Volatility17dComputer,
        Volatility20dComputer,
    )
    from quant_etf_api.factors.builtins.volume import (
        AmountRatio20dComputer,
        VolumeRatio17dComputer,
        VolumeRatio20dComputer,
    )

    templates: list[FactorTemplate] = []

    # ── 首批可配模板 ────────────────────────────────────────────────────
    templates.append(
        build_template(
            "sma",
            lambda p: MAComputer(period=int(p["period"])),
            parameter_schema={"period": _SMA_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日均线"),
            name="简单移动平均线",
            description="收盘价的 N 日简单移动平均，用于趋势判定与均线比较。",
            category="technical",
        )
    )
    templates.append(
        build_template(
            "return",
            lambda p: ReturnComputer(period=int(p["period"])),
            parameter_schema={"period": _RETURN_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日收益率"),
            name="N 日累计收益率",
            description="指数近 N 个交易日的收盘价涨跌幅（%），衡量动量。",
            category="momentum",
        )
    )
    templates.append(
        build_template(
            "return_std",
            lambda p: ReturnStdComputer(period=int(p["period"])),
            parameter_schema={"period": _RETURN_STD_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日收益率标准差"),
            name="N 日收益率标准差",
            description="近 N 个交易日日收益率的标准差（不年化），衡量波动水平。",
            category="volatility",
        )
    )
    templates.append(
        build_template(
            "rsi",
            lambda p: RSIComputer(period=int(p["period"])),
            parameter_schema={"period": _RSI_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日RSI"),
            name="相对强弱指标",
            description="近 N 日上涨与下跌幅度之比映射到 0-100 的相对强弱指标。",
            category="technical",
        )
    )
    templates.append(
        build_template(
            "atr",
            lambda p: ATRComputer(period=int(p["period"])),
            parameter_schema={"period": _ATR_PERIOD},
            compute_lookback_days=_period_lookback,
            instance_label=_period_label("日ATR"),
            name="平均真实波幅",
            description="近 N 日真实波幅均值，衡量价格波动幅度。",
            category="technical",
        )
    )

    # ── 零参数模板 ──────────────────────────────────────────────────────
    zero_param: list[tuple[str, Any]] = [
        ("close_price", lambda _p: ClosePriceComputer()),
        ("change_pct", lambda _p: ChangePctComputer()),
        ("volume_ratio_17d", lambda _p: VolumeRatio17dComputer()),
        ("volume_ratio_20d", lambda _p: VolumeRatio20dComputer()),
        ("amount_ratio_20d", lambda _p: AmountRatio20dComputer()),
        ("breadth_ma20_pct", lambda _p: BreadthMA20Computer()),
        ("index_diffusion_ratio", lambda _p: IndexDiffusionRatioComputer()),
        ("rrg_industry_match_score", lambda _p: RRGIndustryMatchComputer()),
        ("pmi_momentum_3m", lambda _p: PMIMomentumComputer()),
        ("monthly_ma_5m", lambda _p: MonthlyMAComputer(period=5)),
        ("monthly_ma_10m", lambda _p: MonthlyMAComputer(period=10)),
        ("monthly_return_2m", lambda _p: MonthlyReturnComputer(period=2)),
        ("monthly_return_3m", lambda _p: MonthlyReturnComputer(period=3)),
        ("monthly_up_streak", lambda _p: MonthlyStreakComputer()),
        ("sharpe_60d", lambda _p: Sharpe60dComputer()),
        (
            "low_amplitude_momentum_160d_70pct",
            lambda _p: LowAmplitudeMomentumComputer(),
        ),
        ("rsrs", lambda _p: RsrsComputer()),
        ("price_position_ir_60d", lambda _p: PricePositionIrComputer(period=60)),
        ("days_down_up", lambda _p: DaysDownUpComputer()),
        ("ma60d_deviation", lambda _p: MADeviationComputer(period=60)),
        ("donchian_17d_high", lambda _p: DonchianHighComputer(period=17)),
        ("donchian_20d_high", lambda _p: DonchianHighComputer(period=20)),
        ("donchian_17d_low", lambda _p: DonchianLowComputer(period=17)),
        ("donchian_20d_low", lambda _p: DonchianLowComputer(period=20)),
        ("max_drawdown_60d", lambda _p: MaxDrawdown60dComputer()),
        ("drawdown_current", lambda _p: DrawdownCurrentComputer()),
        (
            "days_beyond_upper_lower_21d",
            lambda _p: DaysBeyondUpperLowerComputer(period=21),
        ),
        ("pe_percentile", lambda _p: PEPercentileComputer()),
        ("pb_percentile", lambda _p: PBPercentileComputer()),
        ("volatility_17d", lambda _p: Volatility17dComputer()),
        ("volatility_20d", lambda _p: Volatility20dComputer()),
        ("high_low_63d", lambda _p: HighLowRangeComputer(period=63)),
        ("high_low_21d", lambda _p: HighLowRangeComputer(period=21)),
        ("erp", lambda _p: ERPComputer()),
        ("erp_percentile", lambda _p: ERPPercentileComputer()),
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
