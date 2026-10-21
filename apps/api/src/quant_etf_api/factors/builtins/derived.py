"""派生因子计算器（``expr`` 模板）：在已有因子之上按表达式组合。

派生因子不直接读行情：依赖因子由注册表解析时绑定到计算器上
（:meth:`ExpressionComputer.bind_dependencies`），``FactorComputeService.compute_matrix``
先算全部依赖，再逐日逐指数按表达式求值。因子值同样只现算、不落库。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, Any

from quant_etf_api.factors.base import (
    DEFAULT_INDEX_FACTOR_USAGE,
    VALUE_SHAPE_ASSET,
    FactorContext,
    FactorSpec,
    FactorValue,
)
from quant_etf_api.factors.expression import (
    ParsedExpression,
    evaluate_expression,
    expression_fingerprint,
    parse_expression,
)

if TYPE_CHECKING:
    from quant_etf_api.factors.catalog import FactorInstance

DERIVED_TEMPLATE_ID = "expr"
# 模板默认表达式（只用于模板目录的默认口径探测，策略必须显式给出表达式）
DEFAULT_EXPRESSION = "close_price"


class ExpressionComputer:
    """派生因子计算器：持有解析后的表达式与已绑定的依赖实例。"""

    def __init__(self, expression: str) -> None:
        """解析表达式。

        Args:
            expression: 表达式文本。

        Raises:
            ExpressionError: 表达式不合法。
        """
        self._parsed = parse_expression(expression)
        self._dependencies: dict[str, FactorInstance] = {}

    @property
    def parsed(self) -> ParsedExpression:
        """解析后的表达式。"""
        return self._parsed

    @property
    def refs(self) -> tuple[str, ...]:
        """表达式引用的因子。"""
        return self._parsed.refs

    @property
    def dependencies(self) -> Mapping[str, FactorInstance]:
        """已绑定的依赖实例（引用名 → 实例）。"""
        return dict(self._dependencies)

    def bind_dependencies(self, dependencies: Mapping[str, FactorInstance]) -> None:
        """绑定依赖实例（由注册表在解析时调用）。

        Args:
            dependencies: 引用名 → 已解析的因子实例。
        """
        self._dependencies = dict(dependencies)

    def fingerprint(self) -> str:
        """派生因子指纹（与别名命名无关），研究台账据此判定是否已做诊断。"""
        keys: dict[str, str] = {}
        for ref, instance in self._dependencies.items():
            if isinstance(instance.computer, ExpressionComputer):
                keys[ref] = f"expr:{instance.computer.fingerprint()}"
            else:
                template_id, params_text = instance.dedup_key
                keys[ref] = f"{template_id}:{params_text}"
        return expression_fingerprint(self._parsed.text, keys)

    def evaluate(self, lookup: Any) -> float | None:
        """按依赖值求值。

        Args:
            lookup: 引用名 → 值的函数。

        Returns:
            结果，缺失或数学错误时为 None。
        """
        return evaluate_expression(self._parsed, lookup)

    @property
    def spec(self) -> FactorSpec:
        """派生因子元数据：不直接依赖数据源、回望为 0（由依赖因子各自承担）。"""
        return FactorSpec(
            factor_id=DERIVED_TEMPLATE_ID,
            name="派生因子表达式",
            category="derived",
            version="1.0.0",
            description=(
                "在已有因子之上做四则与函数组合（abs/min/max/log/sqrt/sign），"
                "如 dev250 - dev60；依赖因子先现算，再逐日逐指数求值，不落库。"
            ),
            required_data=[],
            lookback_days=0,
            value_shape=VALUE_SHAPE_ASSET,
            usage=list(DEFAULT_INDEX_FACTOR_USAGE),
            default_params={"expr": DEFAULT_EXPRESSION},
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """派生因子不单独计算：由 compute_matrix 在依赖算完后统一求值。

        Raises:
            RuntimeError: 被当作普通计算器调用。
        """
        raise RuntimeError("派生因子应由 FactorComputeService.compute_matrix 统一求值")


def with_dependencies(instances: Mapping[str, FactorInstance]) -> dict[str, FactorInstance]:
    """把派生因子绑定的依赖（含嵌套）并入实例集合。

    数据准备（面板、估值预加载、全市场行情、回望窗口）与口径指纹都要看到依赖：
    派生因子自身不读数据、回望为 0，只看顶层实例会漏掉依赖所需的数据。

    Args:
        instances: {引用名: 实例}。

    Returns:
        新字典：原实例在前，依赖按首次出现补在后面；请求中已有的同名实例优先。
    """
    result: dict[str, FactorInstance] = dict(instances)
    pending = list(instances.values())
    while pending:
        computer = pending.pop().computer
        if not isinstance(computer, ExpressionComputer):
            continue
        for ref, dependency in computer.dependencies.items():
            if ref not in result:
                result[ref] = dependency
                pending.append(dependency)
    return result
