"""因子模板：参数模式、参数规范化与计算器工厂。

一个模板描述「如何计算一类因子」，而不是「某一个固定参数的因子」。模板持有的
``parameter_schema`` 声明可覆盖参数及其类型与取值范围，``default_params`` 给出该
模板的规范口径，``create_computer`` 按规范化参数创建实际计算器。

模板是因子对外元数据的唯一来源（名称、类别、版本、依赖数据、值形态、适用位置），
计算器自述的 ``FactorSpec`` 只服务于计算器内部（日志与 payload），不参与对外标识。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from quant_etf_api.factors.base import (
    VALUE_SHAPE_ASSET,
    FactorComputer,
    FactorSpec,
    ValueShape,
)

# 参数类型：integer=整数周期类，number=浮点比例类
PARAM_TYPE_INTEGER = "integer"
PARAM_TYPE_NUMBER = "number"
ParameterType = Literal["integer", "number"]


class FactorParameterError(ValueError):
    """模板参数不合法（未知参数、类型错误或超出取值范围）。"""


@dataclass(frozen=True, eq=False)
class ParameterSpec:
    """模板单个可覆盖参数的声明。

    Attributes:
        type: 参数类型，integer 或 number。
        minimum: 允许的最小值（含），None 表示不设下界。
        maximum: 允许的最大值（含），None 表示不设上界。
        default: 默认值，未显式给参数时使用。
        description: 参数中文说明，用于配置校验提示与前端表单。
    """

    type: ParameterType
    minimum: int | float | None
    maximum: int | float | None
    default: int | float
    description: str

    def coerce(self, name: str, value: Any) -> int | float:
        """校验并规范化单个参数取值。

        Args:
            name: 参数名，用于错误信息。
            value: 原始取值（来自策略配置或接口查询串）。

        Returns:
            规范化后的取值：integer 返回 int，number 返回 float。

        Raises:
            FactorParameterError: 取值不是有限数值、类型不符或超出取值范围。
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FactorParameterError(f"参数 {name} 必须是数值，实际为 {value!r}")

        if self.type == PARAM_TYPE_INTEGER:
            if isinstance(value, float):
                if not value.is_integer():
                    raise FactorParameterError(f"参数 {name} 必须是整数，实际为 {value!r}")
                coerced: int | float = int(value)
            else:
                coerced = int(value)
        else:
            coerced = float(value)

        if self.minimum is not None and coerced < self.minimum:
            raise FactorParameterError(f"参数 {name} 不能小于 {self.minimum}，实际为 {coerced}")
        if self.maximum is not None and coerced > self.maximum:
            raise FactorParameterError(f"参数 {name} 不能大于 {self.maximum}，实际为 {coerced}")
        return coerced

    def to_payload(self) -> dict[str, Any]:
        """转换为接口响应字典。

        Returns:
            含 type/minimum/maximum/default/description 的字典。
        """
        return {
            "type": self.type,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "default": self.default,
            "description": self.description,
        }


@dataclass(frozen=True, eq=False)
class FactorTemplate:
    """因子模板：参数模式 + 默认口径 + 计算器工厂 + 元数据。

    Attributes:
        template_id: 模板唯一标识，即 factor_definition.factor_id。
        name: 模板中文名称。
        category: 因子类别。
        version: 模板语义化版本。
        description: 计算逻辑说明。
        required_data: 依赖的数据源列表。
        value_shape: 因子值形态：asset=每资产值，market=市场级值。
        usage: 适用位置数组：timing/score/filter/rank。
        market_scope: 是否需要在全市场指数范围上计算（如市场宽度类因子）。
        parameter_schema: 可覆盖参数声明，空字典表示零参数模板。
        default_params: 模板规范参数，零参数模板记录其固定口径。
        create_computer: 按规范化参数创建计算器的工厂。
        base_lookback_days: 默认参数口径下的回望自然日数。
        compute_lookback_days: 按实际参数推导回望自然日数；None 表示用 base_lookback_days。
        instance_label: 按实际参数生成实例展示名；None 表示用模板名称。
    """

    template_id: str
    name: str
    category: str
    version: str
    description: str
    required_data: tuple[str, ...]
    value_shape: ValueShape
    usage: tuple[str, ...]
    market_scope: bool
    parameter_schema: Mapping[str, ParameterSpec]
    default_params: Mapping[str, Any]
    create_computer: Callable[[Mapping[str, Any]], FactorComputer]
    base_lookback_days: int
    compute_lookback_days: Callable[[Mapping[str, Any]], int] | None = None
    instance_label: Callable[[Mapping[str, Any]], str] | None = None

    @property
    def tunable(self) -> bool:
        """模板是否存在可覆盖参数。"""
        return bool(self.parameter_schema)

    def resolve_params(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """校验并规范化参数：补齐默认值、拒绝未声明参数、校验类型与范围。

        模板固有参数（``default_params`` 中未被 parameter_schema 声明的键）原样保留，
        其余键与显式覆盖一并按参数模式校验，保证默认口径自身也是合法的。

        Args:
            params: 待校验的参数覆盖，None 或空字典表示全部使用默认口径。

        Returns:
            规范化后的完整参数字典（含 default_params 中不可覆盖的固定项）。

        Raises:
            FactorParameterError: 出现未声明参数名，或取值类型/范围不合法。
        """
        supplied = dict(params or {})
        unknown = sorted(set(supplied) - set(self.parameter_schema))
        if unknown:
            raise FactorParameterError(
                f"模板 {self.template_id} 不接受参数 {unknown}，"
                f"可配置参数：{sorted(self.parameter_schema)}"
            )
        resolved: dict[str, Any] = {}
        for name, value in self.default_params.items():
            parameter = self.parameter_schema.get(name)
            resolved[name] = value if parameter is None else parameter.coerce(name, value)
        for name, value in supplied.items():
            resolved[name] = self.parameter_schema[name].coerce(name, value)
        return resolved

    def normalize_text(self, params: Mapping[str, Any] | None = None) -> str:
        """返回规范化参数的稳定 JSON 文本，用于同参去重与报告展示。

        Args:
            params: 已规范化的参数，None 表示默认口径。

        Returns:
            键有序、分隔符固定的 JSON 字符串。
        """
        resolved = dict(self.default_params if params is None else params)
        return json.dumps(resolved, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def lookback_days(self, params: Mapping[str, Any] | None = None) -> int:
        """按实际参数推导回望自然日数。

        Args:
            params: 已规范化的参数，None 表示默认口径。

        Returns:
            回望自然日数。
        """
        resolved = dict(self.default_params if params is None else params)
        if self.compute_lookback_days is None:
            return self.base_lookback_days
        return int(self.compute_lookback_days(resolved))

    def build_computer(self, params: Mapping[str, Any] | None = None) -> FactorComputer:
        """按规范化参数创建计算器。

        Args:
            params: 已规范化的参数，None 表示默认口径。

        Returns:
            实现 FactorComputer 协议的计算器。
        """
        resolved = dict(self.default_params if params is None else params)
        return self.create_computer(resolved)

    def label(self, params: Mapping[str, Any] | None = None) -> str:
        """按实际参数生成实例展示名。

        Args:
            params: 已规范化的参数，None 表示默认口径。

        Returns:
            实例展示名；模板未声明命名规则时返回模板名称。
        """
        if self.instance_label is None:
            return self.name
        resolved = dict(self.default_params if params is None else params)
        return self.instance_label(resolved)

    def spec(self) -> FactorSpec:
        """返回模板默认口径的因子元数据描述符。

        供面板装配判定与所需数据推导使用；模板是元数据的来源，计算器的
        自述 spec 不参与对外标识。

        Returns:
            FactorSpec。
        """
        return FactorSpec(
            factor_id=self.template_id,
            name=self.name,
            category=self.category,
            version=self.version,
            description=self.description,
            required_data=list(self.required_data),
            lookback_days=self.lookback_days(),
            market_scope=self.market_scope,
            value_shape=self.value_shape,
            usage=list(self.usage),
            default_params=dict(self.default_params),
        )


def build_template(
    template_id: str,
    create_computer: Callable[[Mapping[str, Any]], FactorComputer],
    *,
    parameter_schema: Mapping[str, ParameterSpec] | None = None,
    default_params: Mapping[str, Any] | None = None,
    compute_lookback_days: Callable[[Mapping[str, Any]], int] | None = None,
    instance_label: Callable[[Mapping[str, Any]], str] | None = None,
    name: str | None = None,
    category: str | None = None,
    version: str | None = None,
    description: str | None = None,
    required_data: tuple[str, ...] | None = None,
    value_shape: ValueShape | None = None,
    usage: tuple[str, ...] | None = None,
    market_scope: bool | None = None,
) -> FactorTemplate:
    """构造因子模板。

    未显式声明的元数据取自「默认口径计算器」自述的 FactorSpec，避免同一份
    元数据在模板与计算器两处维护；可参数化模板的名称、说明与回望窗口随参数
    变化，必须显式声明。

    Args:
        template_id: 模板唯一标识。
        create_computer: 按规范化参数创建计算器的工厂。
        parameter_schema: 可覆盖参数声明，None 表示零参数模板。
        default_params: 模板规范参数；None 时由 parameter_schema 的默认值构成。
        compute_lookback_days: 按参数推导回望窗口；None 表示用默认口径的窗口。
        instance_label: 按参数生成实例展示名；None 表示用模板名称。
        name: 模板中文名称，None 表示取默认口径计算器的名称。
        category: 因子类别，None 表示取默认口径计算器的类别。
        version: 模板版本，None 表示取默认口径计算器的版本。
        description: 计算逻辑说明，None 表示取默认口径计算器的说明。
        required_data: 依赖数据源，None 表示取默认口径计算器的依赖。
        value_shape: 因子值形态，None 表示取默认口径计算器的形态。
        usage: 适用位置，None 表示取默认口径计算器的适用位置。
        market_scope: 是否需全市场行情，None 表示取默认口径计算器的声明。

    Returns:
        构建完成的 FactorTemplate。

    Raises:
        FactorParameterError: 默认参数不满足自身的 parameter_schema。
    """
    schema = dict(parameter_schema or {})
    defaults = (
        dict(default_params)
        if default_params is not None
        else {key: spec.default for key, spec in schema.items()}
    )

    def _assemble(
        resolved_defaults: Mapping[str, Any],
        base_spec: FactorSpec | None,
    ) -> FactorTemplate:
        """按已解析的默认口径与计算器自述元数据组装模板。"""
        return FactorTemplate(
            template_id=template_id,
            name=name if name is not None else (base_spec.name if base_spec else template_id),
            category=category
            if category is not None
            else (base_spec.category if base_spec else "other"),
            version=version
            if version is not None
            else (base_spec.version if base_spec else "1.0.0"),
            description=description
            if description is not None
            else (base_spec.description if base_spec else ""),
            required_data=(
                tuple(required_data)
                if required_data is not None
                else (tuple(base_spec.required_data) if base_spec else ())
            ),
            value_shape=value_shape
            if value_shape is not None
            else (base_spec.value_shape if base_spec else VALUE_SHAPE_ASSET),
            usage=tuple(usage)
            if usage is not None
            else (tuple(base_spec.usage) if base_spec else ()),
            market_scope=(
                bool(market_scope)
                if market_scope is not None
                else (bool(base_spec.market_scope) if base_spec else False)
            ),
            parameter_schema=schema,
            default_params=resolved_defaults,
            create_computer=create_computer,
            base_lookback_days=int(base_spec.lookback_days) if base_spec else 0,
            compute_lookback_days=compute_lookback_days,
            instance_label=instance_label,
        )

    # 默认口径必须通过自身校验，否则模板一注册就是坏配置
    probe = _assemble(defaults, None)
    probe.resolve_params({})
    base_spec = probe.build_computer().spec
    # 零参数模板的 default_params 记录计算器自述的固定口径
    final_defaults = defaults if defaults else dict(base_spec.default_params or {})
    return _assemble(final_defaults, base_spec)
