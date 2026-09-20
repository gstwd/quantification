"""因子模板与实例解析单元测试。

覆盖模板目录结构、参数规范化与校验、回望窗口推导、模板级元数据，
以及「别名 → 模板 + 参数」的解析规则。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from quant_etf_api.engine.config import FactorAliasConfig
from quant_etf_api.factors.base import FactorSpec
from quant_etf_api.factors.catalog import (
    FactorResolutionError,
    FactorTemplateRegistry,
    build_default_registry,
)
from quant_etf_api.factors.compute import FactorComputeService
from quant_etf_api.factors.templates import (
    FactorParameterError,
    FactorTemplate,
    ParameterSpec,
    build_template,
)


class _StubComputer:
    """只暴露 spec 的桩计算器，用于构造模板。"""

    def __init__(self, factor_id: str, lookback_days: int = 30) -> None:
        """初始化桩计算器。"""
        self._factor_id = factor_id
        self._lookback_days = lookback_days

    @property
    def spec(self) -> FactorSpec:
        """返回因子元数据。"""
        return FactorSpec(
            factor_id=self._factor_id,
            name=f"{self._factor_id} 名称",
            category="momentum",
            version="1.2.3",
            description="桩说明",
            required_data=["index_bars"],
            lookback_days=self._lookback_days,
        )


class _Alias:
    """最小的别名替身（template_id + params）。"""

    def __init__(self, template_id: str, params: dict[str, Any]) -> None:
        """保存模板与参数。"""
        self.template_id = template_id
        self.params = params


class TestParameterSpec:
    """单参数声明与规范化。"""

    def test_integer_accepts_integral_float(self) -> None:
        """整数参数接受整数值浮点并归一为 int。"""
        spec = ParameterSpec("integer", 2, 10, 5, "周期")
        assert spec.coerce("period", 7.0) == 7

    def test_integer_rejects_fraction(self) -> None:
        """整数参数拒绝带小数的取值。"""
        spec = ParameterSpec("integer", 2, 10, 5, "周期")
        with pytest.raises(FactorParameterError):
            spec.coerce("period", 7.5)

    def test_number_accepts_float(self) -> None:
        """数值参数接受浮点。"""
        spec = ParameterSpec("number", 0.0, 1.0, 0.5, "比例")
        assert spec.coerce("ratio", 0.25) == 0.25

    def test_rejects_boolean(self) -> None:
        """布尔不是合法数值参数。"""
        spec = ParameterSpec("integer", 1, 10, 5, "周期")
        with pytest.raises(FactorParameterError):
            spec.coerce("period", True)

    def test_rejects_out_of_range(self) -> None:
        """取值超出声明范围时报错，边界值含端点。"""
        spec = ParameterSpec("integer", 2, 10, 5, "周期")
        assert spec.coerce("period", 2) == 2
        assert spec.coerce("period", 10) == 10
        with pytest.raises(FactorParameterError):
            spec.coerce("period", 1)
        with pytest.raises(FactorParameterError):
            spec.coerce("period", 11)

    def test_payload_shape(self) -> None:
        """接口载荷包含前端渲染所需字段。"""
        spec = ParameterSpec("integer", 2, 250, 20, "均线周期")
        assert spec.to_payload() == {
            "type": "integer",
            "minimum": 2,
            "maximum": 250,
            "default": 20,
            "description": "均线周期",
        }


def _tunable() -> FactorTemplate:
    """构造一个可调参模板（period 2–250，默认 20）。"""
    return build_template(
        "tpl_sma",
        lambda params: _StubComputer(f"stub_{params['period']}", 35),
        parameter_schema={"period": ParameterSpec("integer", 2, 250, 20, "周期")},
        compute_lookback_days=lambda params: max(15, int(params["period"] * 1.5) + 5),
        instance_label=lambda params: f"{params['period']}日",
        name="均线模板",
        category="technical",
    )


class TestFactorTemplate:
    """模板参数处理与元数据。"""

    def test_default_params_filled(self) -> None:
        """未给参数时补齐模板默认值。"""
        template = _tunable()
        assert template.resolve_params({}) == {"period": 20}
        assert template.tunable is True

    def test_override_applied(self) -> None:
        """显式参数覆盖默认值。"""
        assert _tunable().resolve_params({"period": 60}) == {"period": 60}

    def test_unknown_parameter_rejected(self) -> None:
        """未声明的参数名直接拒绝。"""
        with pytest.raises(FactorParameterError):
            _tunable().resolve_params({"window": 10})

    def test_zero_param_template_rejects_any_parameter(self) -> None:
        """零参数模板不接受任何参数覆盖。"""
        template = build_template("tpl_fixed", lambda _params: _StubComputer("stub_fixed"))
        assert template.tunable is False
        assert template.resolve_params({}) == {}
        with pytest.raises(FactorParameterError):
            template.resolve_params({"period": 5})

    def test_invalid_default_rejected_at_build(self) -> None:
        """默认参数不满足自身参数模式时构造即失败。"""
        with pytest.raises(FactorParameterError):
            build_template(
                "tpl_bad",
                lambda _params: _StubComputer("stub_bad"),
                parameter_schema={"period": ParameterSpec("integer", 2, 10, 20, "周期")},
            )

    def test_lookback_depends_on_params(self) -> None:
        """回望窗口按实际参数推导。"""
        template = _tunable()
        assert template.lookback_days({"period": 20}) == 35
        assert template.lookback_days({"period": 60}) == 95

    def test_lookback_falls_back_to_computer_spec(self) -> None:
        """未声明推导函数时回望取计算器自述值。"""
        template = build_template("tpl_fixed", lambda _params: _StubComputer("stub_fixed", 77))
        assert template.lookback_days({}) == 77

    def test_normalize_text_is_stable(self) -> None:
        """规范化文本键有序，可用于同参去重。"""
        assert _tunable().normalize_text({"period": 20}) == '{"period":20}'

    def test_label_uses_params(self) -> None:
        """实例展示名按参数生成。"""
        assert _tunable().label({"period": 60}) == "60日"

    def test_metadata_derived_from_computer(self) -> None:
        """未显式声明的元数据取自计算器自述 spec。"""
        spec = build_template("tpl_fixed", lambda _params: _StubComputer("stub_fixed", 30)).spec()
        assert spec.name == "stub_fixed 名称"
        assert spec.category == "momentum"
        assert spec.version == "1.2.3"
        assert spec.required_data == ["index_bars"]
        assert spec.lookback_days == 30

    def test_zero_param_defaults_record_computer_params(self) -> None:
        """零参数模板的 default_params 记录计算器声明的固定口径。"""

        class _ParamComputer(_StubComputer):
            @property
            def spec(self) -> FactorSpec:
                spec = super().spec
                spec.default_params = {"window": 160}
                return spec

        template = build_template(
            "tpl_fixed", lambda _params: _ParamComputer("stub_fixed", 30)
        )
        assert template.default_params == {"window": 160}

    def test_computer_receives_resolved_params(self) -> None:
        """计算器工厂收到的是补齐默认值后的参数。"""
        received: list[dict[str, Any]] = []

        def factory(params: dict[str, Any]) -> _StubComputer:
            received.append(dict(params))
            return _StubComputer("stub")

        template = build_template(
            "tpl_x",
            factory,
            parameter_schema={"period": ParameterSpec("integer", 2, 250, 20, "周期")},
        )
        template.build_computer({"period": 30})
        assert received[-1] == {"period": 30}


class TestFactorTemplateRegistry:
    """模板注册表与引用解析。"""

    def test_default_registry_shape(self) -> None:
        """默认注册表包含 40 个模板，其中 5 个可调参。"""
        registry = build_default_registry()
        assert len(registry.all()) == 40
        assert sum(1 for template in registry.all() if template.tunable) == 5
        assert set(registry.ids()) >= {"sma", "return", "return_std", "rsi", "atr"}

    def test_register_rejects_duplicate(self) -> None:
        """重复登记同一 template_id 报错。"""
        registry = FactorTemplateRegistry()
        template = build_template("tpl_a", lambda _params: _StubComputer("stub_a"))
        registry.register(template)
        with pytest.raises(ValueError):
            registry.register(template)

    def test_alias_wins_over_template_id(self) -> None:
        """同名时别名声明优先于模板默认口径。"""
        aliases = {"sma": FactorAliasConfig(template_id="sma", params={"period": 60})}
        instance = build_default_registry().resolve("sma", aliases)
        assert instance.template_id == "sma"
        assert instance.params["period"] == 60

    def test_bare_template_uses_defaults(self) -> None:
        """未声明别名时用模板默认参数。"""
        instance = build_default_registry().resolve("sma")
        assert instance.instance_id == "sma"
        assert instance.params == {"period": 20}

    def test_unknown_reference_raises(self) -> None:
        """既非别名也非模板 ID 的引用解析失败。"""
        with pytest.raises(FactorResolutionError):
            build_default_registry().resolve("ghost")

    def test_alias_to_unknown_template_raises(self) -> None:
        """别名指向未注册模板时解析失败。"""
        aliases = {"trend": FactorAliasConfig(template_id="no_such", params={})}
        with pytest.raises(FactorResolutionError):
            build_default_registry().resolve("trend", aliases)

    def test_out_of_range_param_raises_resolution_error(self) -> None:
        """参数超范围时以解析错误上抛，便于接口映射为 422。"""
        aliases = {"trend": FactorAliasConfig(template_id="sma", params={"period": 9999})}
        with pytest.raises(FactorResolutionError):
            build_default_registry().resolve("trend", aliases)

    def test_resolve_params_keeps_parameter_error_type(self) -> None:
        """显式模板 + 参数入口把参数错误保留为 FactorParameterError。"""
        registry = build_default_registry()
        assert registry.resolve_params("sma", {"period": 10}).params["period"] == 10
        with pytest.raises(FactorParameterError):
            registry.resolve_params("sma", {"period": 1})
        with pytest.raises(FactorResolutionError):
            registry.resolve_params("ghost", {})

    def test_dedup_key_groups_same_template_params(self) -> None:
        """同模板同参数的别名共享去重键，不同参数分开。"""
        aliases = {
            "fast_a": FactorAliasConfig(template_id="sma", params={"period": 10}),
            "fast_b": FactorAliasConfig(template_id="sma", params={"period": 10}),
            "slow": FactorAliasConfig(template_id="sma", params={"period": 30}),
        }
        resolved = build_default_registry().resolve_all(["fast_a", "fast_b", "slow"], aliases)
        assert resolved["fast_a"].dedup_key == resolved["fast_b"].dedup_key
        assert resolved["fast_a"].dedup_key != resolved["slow"].dedup_key

    def test_lookback_of_instance(self) -> None:
        """实例回望窗口按参数推导。"""
        aliases = {"slow": FactorAliasConfig(template_id="sma", params={"period": 60})}
        assert build_default_registry().resolve("slow", aliases).lookback_days == 95

    def test_max_lookback_days(self) -> None:
        """批量回望取最大值，空集合回退 90。"""
        registry = build_default_registry()
        instances = registry.resolve_all(["sma", "pe_percentile"])
        assert FactorTemplateRegistry.max_lookback_days(instances.values()) == 730
        assert FactorTemplateRegistry.max_lookback_days([]) == 90

    def test_instance_payload(self) -> None:
        """实例载荷包含模板版本与规范化参数。"""
        payload = build_default_registry().resolve("rsi").to_payload()
        assert payload["template_id"] == "rsi"
        assert payload["template_version"] == "1.0.0"
        assert payload["params"] == {"period": 14}


class TestComputeServiceResolution:
    """现算服务的引用解析。"""

    def test_resolve_delegates_to_registry(self) -> None:
        """现算服务按同一规则解析引用。"""
        service = FactorComputeService(db=None, registry=build_default_registry())  # type: ignore[arg-type]
        instance = service.resolve("return", {"return": _Alias("return", {"period": 5})})
        assert instance.template_id == "return"
        assert instance.params["period"] == 5


def test_series_returns_empty_without_bars(monkeypatch) -> None:
    """指数在区间内无行情时时间序列返回空列表。"""
    service = FactorComputeService(db=object(), registry=build_default_registry())  # type: ignore[arg-type]

    class _EmptyRepo:
        def __init__(self, _db: Any) -> None:
            pass

        def find_trading_dates(
            self, start: date, end: date, index_codes: list[str]
        ) -> list[date]:
            return []

    monkeypatch.setattr("quant_etf_api.factors.compute.IndexDailyBarRepository", _EmptyRepo)
    instance = build_default_registry().resolve("close_price")
    assert service.compute_series(instance, "000300", date(2025, 1, 1), date(2025, 1, 31)) == []


def test_series_rejects_reversed_range() -> None:
    """起始日晚于截止日时直接返回空列表，不查询行情。"""
    service = FactorComputeService(db=object(), registry=build_default_registry())  # type: ignore[arg-type]
    instance = build_default_registry().resolve("close_price")
    assert service.compute_series(instance, "000300", date(2025, 2, 1), date(2025, 1, 1)) == []
