"""阶段 4B 派生因子：表达式安全性、指纹、依赖解析、矩阵求值与诊断门槛。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import quant_etf_api.cli as cli
from quant_etf_api.engine.config import FactorAliasConfig, ScoreConfig, StrategyConfig
from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.factors.builtins.derived import ExpressionComputer, with_dependencies
from quant_etf_api.factors.catalog import (
    FactorInstance,
    FactorResolutionError,
    build_default_registry,
)
from quant_etf_api.factors.compute import FactorComputeService, _expand_derived
from quant_etf_api.factors.expression import (
    ExpressionError,
    evaluate_expression,
    parse_expression,
)
from quant_etf_api.factors.templates import build_template
from quant_etf_api.services.research_ledger_service import ResearchLedgerService

_DAY = date(2025, 3, 3)


class TestExpressionSafety:
    """表达式语法白名单。"""

    @pytest.mark.parametrize(
        "text",
        [
            "__import__('os')",
            "a.b",
            "a[0]",
            "a if b else c",
            "a > b",
            "lambda: 1",
            "open('x')",
            "'text' + a",
            "min(a)",
            "abs(a, b)",
            "1 + 2",
            "abs",
            "a" * 201,
        ],
    )
    def test_rejected(self, text: str) -> None:
        """属性、下标、比较、字符串、非白名单函数、无引用等一律拒绝。"""
        with pytest.raises(ExpressionError):
            parse_expression(text)

    def test_refs_and_canonical_text(self) -> None:
        """引用按出现顺序去重；函数名不算引用。"""
        parsed = parse_expression("max(dev250,  dev60) - dev250 / 2")
        assert parsed.refs == ("dev250", "dev60")
        assert parsed.text == "max(dev250, dev60) - dev250 / 2"

    @pytest.mark.parametrize(
        ("text", "values", "expected"),
        [
            ("a - b", {"a": 3.0, "b": 1.0}, 2.0),
            ("a / b", {"a": 1.0, "b": 0.0}, None),
            ("log(a)", {"a": -1.0}, None),
            ("a - b", {"a": 3.0, "b": None}, None),
            ("sign(a) * sqrt(abs(a))", {"a": -4.0}, -2.0),
            ("a ** 1000", {"a": 1e10}, None),
        ],
    )
    def test_evaluate(self, text: str, values: dict[str, Any], expected: float | None) -> None:
        """缺失、除零、定义域错误、溢出都返回 None。"""
        assert evaluate_expression(parse_expression(text), values.get) == expected


def _aliases(**extra: dict[str, Any]) -> dict[str, FactorAliasConfig]:
    """常用别名声明。"""
    base: dict[str, dict[str, Any]] = {
        "dev250": {"template_id": "ma_deviation", "params": {"period": 250}},
        "dev60": {"template_id": "ma_deviation", "params": {"period": 60}},
    }
    base.update(extra)
    return {name: FactorAliasConfig(**decl) for name, decl in base.items()}


class TestResolution:
    """依赖解析与指纹。"""

    def test_dependencies_bound_and_fingerprint_ignores_alias_names(self) -> None:
        """同一组合换个别名名字，指纹不变；参数不同则指纹不同。"""
        registry = build_default_registry()
        a = registry.resolve(
            "spread", _aliases(spread={"template_id": "expr", "params": {"expr": "dev250 - dev60"}})
        )
        b = registry.resolve(
            "gap",
            _aliases(
                long={"template_id": "ma_deviation", "params": {"period": 250}},
                gap={"template_id": "expr", "params": {"expr": "long - dev60"}},
            ),
        )
        c = registry.resolve(
            "spread",
            _aliases(
                dev60={"template_id": "ma_deviation", "params": {"period": 20}},
                spread={"template_id": "expr", "params": {"expr": "dev250 - dev60"}},
            ),
        )
        assert set(a.computer.dependencies) == {"dev250", "dev60"}
        assert a.computer.fingerprint() == b.computer.fingerprint()
        assert a.computer.fingerprint() != c.computer.fingerprint()

    def test_cycles_and_unknown_refs_rejected(self) -> None:
        """循环引用与未知引用在解析时报错。"""
        registry = build_default_registry()
        with pytest.raises(FactorResolutionError, match="循环"):
            registry.resolve(
                "x",
                _aliases(
                    x={"template_id": "expr", "params": {"expr": "y + 1"}},
                    y={"template_id": "expr", "params": {"expr": "x - 1"}},
                ),
            )
        with pytest.raises(FactorResolutionError):
            registry.resolve(
                "x", _aliases(x={"template_id": "expr", "params": {"expr": "nope_alias * 2"}})
            )


class _ConstComputer:
    """测试用计算器：按 (指数, 值) 表返回常量。"""

    def __init__(self, factor_id: str, values: dict[str, float | None]) -> None:
        self._id = factor_id
        self._values = values

    @property
    def spec(self) -> FactorSpec:
        return FactorSpec(
            factor_id=self._id,
            name=self._id,
            category="test",
            version="1",
            description="",
            required_data=[],
            lookback_days=0,
        )

    def compute(self, index_code: str, trade_date: date, ctx: Any) -> FactorValue:
        return FactorValue(factor_id=self._id, numeric=self._values.get(index_code))


def _const_instance(instance_id: str, values: dict[str, float | None]) -> FactorInstance:
    """构造常量因子实例。"""
    template = build_template(
        f"t_{instance_id}",
        lambda _p, v=values, i=instance_id: _ConstComputer(i, v),
        name=instance_id,
    )
    return FactorInstance(
        instance_id=instance_id,
        label=instance_id,
        template=template,
        params={},
        computer=_ConstComputer(instance_id, values),
    )


def _derived(instance_id: str, expr: str, deps: dict[str, FactorInstance]) -> FactorInstance:
    """构造已绑定依赖的派生实例。"""
    computer = ExpressionComputer(expr)
    computer.bind_dependencies(deps)
    template = build_default_registry().get("expr")
    assert template is not None
    return FactorInstance(
        instance_id=instance_id,
        label=expr,
        template=template,
        params={"expr": expr},
        computer=computer,
    )


class TestMatrixEvaluation:
    """compute_matrix 的派生求值。"""

    def test_expand_orders_dependencies_first(self) -> None:
        """依赖先于派生、请求中已有的同名实例复用；派生引用派生按依赖顺序。"""
        a = _const_instance("a", {})
        b = _const_instance("b", {})
        inner = _derived("inner", "a - b", {"a": a, "b": b})
        outer = _derived("outer", "inner * 2", {"inner": inner})
        base, derived = _expand_derived([outer, a])
        assert [i.instance_id for i in base] == ["a", "b"]
        assert [i.instance_id for i in derived] == ["inner", "outer"]

    def test_compute_matrix_fills_derived(self) -> None:
        """派生值按依赖逐指数求出；依赖缺失的指数为 None。"""
        a = _const_instance("a", {"X": 3.0, "Y": None})
        b = _const_instance("b", {"X": 1.0, "Y": 2.0})
        spread = _derived("spread", "a - b", {"a": a, "b": b})
        service = FactorComputeService.__new__(FactorComputeService)
        ctx = FactorContext()
        matrix = service.compute_matrix([spread], ["X", "Y"], [_DAY], ctx=ctx)
        day = matrix.values[_DAY]
        assert day[("X", "spread")] == 2.0
        assert day[("Y", "spread")] is None
        assert day[("X", "a")] == 3.0


def _spread_config(**aliases: dict[str, Any]) -> StrategyConfig:
    """评分只引用派生因子 spread 的策略配置（依赖不直接出现在评分里）。"""
    return StrategyConfig(
        strategy_id="s",
        display_name="s",
        score=ScoreConfig(factors={"spread": 1.0}),
        factor_aliases=_aliases(**aliases),
    )


class TestDependencyDataPreparation:
    """派生因子的依赖进入数据准备路径（面板、预加载、口径指纹）。"""

    def test_with_dependencies_adds_nested_and_keeps_requested(self) -> None:
        """嵌套依赖补齐；请求中已有的同名实例优先。"""
        a = _const_instance("a", {})
        b = _const_instance("b", {})
        own_a = _const_instance("a", {"X": 9.0})
        inner = _derived("inner", "a - b", {"a": a, "b": b})
        outer = _derived("outer", "inner * 2", {"inner": inner})
        expanded = with_dependencies({"outer": outer, "a": own_a})
        assert set(expanded) == {"outer", "a", "inner", "b"}
        assert expanded["a"] is own_a

    def test_provider_returns_dependencies(self) -> None:
        """resolve_instances 返回派生因子依赖的面板类模板。"""
        provider = FactorProvider(registry=build_default_registry())
        config = _spread_config(
            diff={"template_id": "index_diffusion_ratio", "params": {}},
            spread={"template_id": "expr", "params": {"expr": "diff - dev60"}},
        )
        instances = provider.resolve_instances(config)
        assert {"spread", "diff", "dev60"} <= set(instances)
        assert instances["diff"].template.template_id == "index_diffusion_ratio"

    def test_fingerprint_tracks_dependency_params(self) -> None:
        """依赖参数变化 → 缓存指纹变化（否则参数扫描变体会命中同一份因子值）。"""
        provider = FactorProvider(registry=build_default_registry())
        spread = {"template_id": "expr", "params": {"expr": "dev250 - dev60"}}
        base = provider.instance_fingerprint(_spread_config(spread=spread))
        changed = provider.instance_fingerprint(
            _spread_config(
                dev60={"template_id": "ma_deviation", "params": {"period": 40}}, spread=spread
            )
        )
        assert base != changed

    def test_panel_context_built_for_derived_dependency(self) -> None:
        """只请求派生因子时，依赖的面板类模板也会触发面板装配。"""
        registry = build_default_registry()
        spread = registry.resolve(
            "spread",
            _aliases(
                diff={"template_id": "index_diffusion_ratio", "params": {}},
                spread={"template_id": "expr", "params": {"expr": "diff * 2"}},
            ),
        )
        service = FactorComputeService.__new__(FactorComputeService)
        service._build_panels = MagicMock(return_value={"calendar": "ok"})  # type: ignore[method-assign]
        panels = service.build_panel_context([spread], ["X"], [_DAY])
        assert panels == {"calendar": "ok"}
        service._build_panels.assert_called_once()


class TestDiagnosticGate:
    """派生因子诊断门槛。"""

    def _config(self) -> StrategyConfig:
        """含一个派生因子的策略配置。"""
        return StrategyConfig(
            strategy_id="s",
            display_name="s",
            score=ScoreConfig(factors={"spread": 1.0}),
            factor_aliases=_aliases(
                spread={"template_id": "expr", "params": {"expr": "dev250 - dev60"}}
            ),
        )

    def _ledger(self, diagnosed: bool) -> ResearchLedgerService:
        """构造 Mock 仓库的台账服务。"""
        svc = ResearchLedgerService(db=MagicMock())
        svc._repo = MagicMock()
        svc._repo.has_diagnostic.return_value = diagnosed
        svc._repo.find_hypothesis.return_value = None
        svc._config_svc = MagicMock()
        return svc

    def test_undiagnosed_blocks(self) -> None:
        """未诊断的派生因子拦截并给出诊断命令提示。"""
        with pytest.raises(ValueError, match="未诊断的派生因子"):
            self._ledger(False).ensure_derived_diagnosed(self._config(), "变体 v1")

    def test_diagnosed_passes(self) -> None:
        """诊断过的派生因子放行。"""
        self._ledger(True).ensure_derived_diagnosed(self._config(), "变体 v1")

    def test_diagnostic_record_not_counted_and_strategyless(self) -> None:
        """诊断记录不计入 N、可以不挂策略；其他试验仍必须挂策略。"""
        svc = self._ledger(False)
        trial = svc.record_derived_diagnostic(
            fingerprint="f" * 64, expression="dev250 - dev60", diagnostic="factor-ic"
        )
        assert trial["kind"] == "diagnostic"
        assert trial["counted_in_n"] is False
        assert trial["ref_id"] == "f" * 64
        with pytest.raises(ValueError, match="归属一个策略"):
            svc.record_trial(kind="manual", strategy_id=None, trial_count=1)

    def test_cli_records_diagnostic_for_derived(self) -> None:
        """诊断对象是派生因子时，CLI 把指纹写入台账并在输出中给出。"""
        registry = build_default_registry()
        instance = registry.resolve(
            "spread", _aliases(spread={"template_id": "expr", "params": {"expr": "dev250 - dev60"}})
        )
        ledger = MagicMock()
        data: dict[str, Any] = {}
        cli._record_derived_diagnostic(
            ledger, instance, SimpleNamespace(strategy_id="s"), "buckets", data
        )
        assert ledger.record_derived_diagnostic.call_args.kwargs["fingerprint"] == (
            instance.computer.fingerprint()
        )
        assert data["derived"]["expression"] == "dev250 - dev60"
        plain = registry.resolve_params("rsi")
        ledger.reset_mock()
        cli._record_derived_diagnostic(
            ledger, plain, SimpleNamespace(strategy_id="s"), "buckets", {}
        )
        ledger.record_derived_diagnostic.assert_not_called()


class TestFactorCatalogPayload:
    """因子目录接口载荷。"""

    def test_text_parameter_serializes(self) -> None:
        """派生因子的 text 参数能通过因子目录响应 schema（否则整个目录接口 500）。"""
        from quant_etf_api.schemas.factor import FactorParameterSpecResponse  # noqa: PLC0415

        template = build_default_registry().get("expr")
        assert template is not None
        payload = template.parameter_schema["expr"].to_payload()
        assert FactorParameterSpecResponse(**payload).type == "text"
