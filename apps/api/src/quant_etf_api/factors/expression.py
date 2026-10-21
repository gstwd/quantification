"""派生因子表达式：在已有因子之上做安全的四则 / 函数组合（``expr`` 模板）。

表达式只允许：数值常量、因子引用（别名或模板 ID，需为合法标识符）、``+ - * / **``、
一元正负号，以及白名单函数 ``abs / min / max / log / sqrt / sign``。不允许属性访问、
下标、比较、布尔运算、lambda 等任何其他语法，因此不会执行任意代码。

求值时任一引用缺失、除零、定义域错误或结果非有限数，都返回 None（与因子缺失同一语义）。
指纹 :func:`expression_fingerprint` 由规范化表达式与各依赖的「模板 + 规范化参数」构成，
与别名命名无关：同一组合换个别名仍是同一个派生因子（研究台账据此判定是否已诊断）。
"""

from __future__ import annotations

import ast
import hashlib
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

# 表达式长度与节点数上限：派生因子应当是可读的少量组合，而不是一段程序
MAX_EXPRESSION_LENGTH = 200
MAX_EXPRESSION_NODES = 60

_BINARY_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a**b,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.USub: lambda a: -a,
    ast.UAdd: lambda a: a,
}


def _sign(value: float) -> float:
    """符号函数：正 1、负 −1、零 0。"""
    return float((value > 0) - (value < 0))


# 函数白名单：名称 → (函数, 最少参数个数, 最多参数个数)
_FUNCTIONS: dict[str, tuple[Callable[..., float], int, int]] = {
    "abs": (abs, 1, 1),
    "min": (min, 2, 8),
    "max": (max, 2, 8),
    "log": (math.log, 1, 1),
    "sqrt": (math.sqrt, 1, 1),
    "sign": (_sign, 1, 1),
}

_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
    *_BINARY_OPS,
    *_UNARY_OPS,
)


class ExpressionError(ValueError):
    """派生因子表达式不合法。"""


@dataclass(frozen=True)
class ParsedExpression:
    """解析后的表达式。

    Attributes:
        text: 规范化文本（``ast.unparse``）。
        tree: 已校验的语法树。
        refs: 引用的因子（按首次出现顺序、去重）。
    """

    text: str
    tree: ast.Expression
    refs: tuple[str, ...]


def parse_expression(text: str) -> ParsedExpression:
    """解析并校验表达式。

    Args:
        text: 表达式文本，如 ``dev250 - dev60``、``amount_ratio / max(vol20, 1)``。

    Returns:
        解析结果。

    Raises:
        ExpressionError: 为空、过长、语法错误或含不允许的语法 / 函数。
    """
    source = (text or "").strip()
    if not source:
        raise ExpressionError("表达式不能为空")
    if len(source) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError(f"表达式不能超过 {MAX_EXPRESSION_LENGTH} 个字符")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"表达式语法错误：{exc.msg}") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_EXPRESSION_NODES:
        raise ExpressionError(f"表达式过于复杂（节点数超过 {MAX_EXPRESSION_NODES}）")
    function_names: set[int] = set()
    refs: list[str] = []
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(f"表达式不允许使用 {type(node).__name__} 语法")
        if isinstance(node, ast.Constant) and (
            isinstance(node.value, bool) or not isinstance(node.value, (int, float))
        ):
            raise ExpressionError("表达式中的常量只能是数值")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                raise ExpressionError(f"只允许函数 {', '.join(sorted(_FUNCTIONS))}")
            if node.keywords:
                raise ExpressionError("函数不支持关键字参数")
            _fn, low, high = _FUNCTIONS[node.func.id]
            if not low <= len(node.args) <= high:
                raise ExpressionError(f"函数 {node.func.id} 需要 {low}~{high} 个参数")
            function_names.add(id(node.func))
    for node in nodes:
        if isinstance(node, ast.Name) and id(node) not in function_names:
            if node.id in _FUNCTIONS:
                raise ExpressionError(f"{node.id} 是函数名，不能作为因子引用")
            if node.id not in refs:
                refs.append(node.id)
    if not refs:
        raise ExpressionError("表达式至少要引用一个因子")
    return ParsedExpression(text=ast.unparse(tree), tree=tree, refs=tuple(refs))


def evaluate_expression(
    parsed: ParsedExpression, lookup: Callable[[str], float | None]
) -> float | None:
    """对一组因子值求表达式。

    Args:
        parsed: 解析结果。
        lookup: 因子引用 → 当日值（缺失为 None）。

    Returns:
        结果；任一引用缺失、除零、定义域错误或非有限数时为 None。
    """

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            value = lookup(node.id)
            if value is None:
                raise _Missing
            return float(value)
        if isinstance(node, ast.BinOp):
            return _BINARY_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            return _UNARY_OPS[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = _FUNCTIONS[node.func.id][0]
            return float(fn(*(_eval(arg) for arg in node.args)))
        raise ExpressionError(f"不支持的语法 {type(node).__name__}")

    try:
        result = _eval(parsed.tree)
    except (_Missing, ZeroDivisionError, ValueError, OverflowError, TypeError):
        return None
    if isinstance(result, complex) or not math.isfinite(result):
        return None
    return result


class _Missing(Exception):
    """求值时遇到缺失的因子值。"""


def expression_fingerprint(text: str, dependencies: Mapping[str, str]) -> str:
    """派生因子的指纹：规范化表达式 + 各依赖的口径键（与别名命名无关）。

    Args:
        text: 规范化表达式文本。
        dependencies: {引用名: 依赖口径键}，口径键为「模板 ID + 规范化参数」或
            嵌套派生因子的指纹。

    Returns:
        64 位十六进制 sha256。
    """
    # 别名换成口径键后再规范化：同一组合换个别名名字得到同一个指纹
    tree = ast.parse(text, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in dependencies:
            node.id = f"__dep_{hashlib.sha256(dependencies[node.id].encode()).hexdigest()[:16]}"
    canonical = ast.unparse(tree)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
