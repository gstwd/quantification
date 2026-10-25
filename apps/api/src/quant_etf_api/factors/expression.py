"""派生因子表达式：在已有因子之上做安全的四则 / 函数组合（``expr`` 模板）。

表达式只允许：数值常量、因子引用（别名或模板 ID，需为合法标识符）、``+ - * / **``、
一元正负号，白名单函数 ``abs / min / max / log / sqrt / sign``，以及时序函数
``delay / delta / ts_mean / ts_std / ts_zscore / ts_rank``（第二个参数为 1~250 的整数
字面量窗口）。不允许属性访问、下标、比较、布尔运算、lambda 等任何其他语法，因此不会
执行任意代码。

求值时任一引用缺失、除零、定义域错误或结果非有限数，都返回 None（与因子缺失同一语义）。
时序函数沿全局交易日轴逐个指数计算，窗口内任一值缺失即缺失（严格口径）；不含时序函数
的表达式逐日逐指数标量求值，含时序函数的表达式按 交易日 × 指数 数组求值。
指纹 :func:`expression_fingerprint` 由规范化表达式与各依赖的「模板 + 规范化参数」构成，
与别名命名无关：同一组合换个别名仍是同一个派生因子（研究台账据此判定是否已诊断）。
"""

from __future__ import annotations

import ast
import hashlib
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import numpy as np

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

# 时序函数白名单：名称 → 窗口最小值。delay/delta 看 n 日前的值；ts_* 的窗口含当日共 n 个值
_SERIES_FUNCTIONS: dict[str, int] = {
    "delay": 1,
    "delta": 1,
    "ts_mean": 2,
    "ts_std": 2,
    "ts_zscore": 2,
    "ts_rank": 2,
}
# 时序窗口上限（交易日）：约一年，配合依赖回望不超过回测预加载
MAX_SERIES_WINDOW = 250

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
        series_window: 时序函数需要的额外交易日数（嵌套时累加）；0 表示不含时序函数。
    """

    text: str
    tree: ast.Expression
    refs: tuple[str, ...]
    series_window: int = 0


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
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name not in _FUNCTIONS and name not in _SERIES_FUNCTIONS:
                allowed = ", ".join(sorted([*_FUNCTIONS, *_SERIES_FUNCTIONS]))
                raise ExpressionError(f"只允许函数 {allowed}")
            if node.keywords:
                raise ExpressionError("函数不支持关键字参数")
            if name in _SERIES_FUNCTIONS:
                _check_series_call(node, name)
            else:
                _fn, low, high = _FUNCTIONS[name]
                if not low <= len(node.args) <= high:
                    raise ExpressionError(f"函数 {name} 需要 {low}~{high} 个参数")
            function_names.add(id(node.func))
    for node in nodes:
        if isinstance(node, ast.Name) and id(node) not in function_names:
            if node.id in _FUNCTIONS or node.id in _SERIES_FUNCTIONS:
                raise ExpressionError(f"{node.id} 是函数名，不能作为因子引用")
            if node.id not in refs:
                refs.append(node.id)
    if not refs:
        raise ExpressionError("表达式至少要引用一个因子")
    window = _series_window(tree)
    if window > MAX_SERIES_WINDOW:
        raise ExpressionError(f"时序函数嵌套后的总窗口不能超过 {MAX_SERIES_WINDOW} 个交易日")
    return ParsedExpression(
        text=ast.unparse(tree), tree=tree, refs=tuple(refs), series_window=window
    )


def _check_series_call(node: ast.Call, name: str) -> None:
    """校验时序函数调用：恰好 2 个参数，第二个为合法范围内的整数字面量。

    Args:
        node: 函数调用节点。
        name: 函数名。

    Raises:
        ExpressionError: 参数个数或窗口不合法。
    """
    if len(node.args) != 2:
        raise ExpressionError(f"函数 {name} 需要 2 个参数：{name}(表达式, 窗口)")
    window = node.args[1]
    low = _SERIES_FUNCTIONS[name]
    if (
        not isinstance(window, ast.Constant)
        or isinstance(window.value, bool)
        or not isinstance(window.value, int)
        or not low <= window.value <= MAX_SERIES_WINDOW
    ):
        raise ExpressionError(
            f"函数 {name} 的窗口必须是 {low}~{MAX_SERIES_WINDOW} 的整数字面量"
        )


def _series_window(node: ast.AST) -> int:
    """时序函数需要的额外交易日数：沿语法树取各路径窗口之和的最大值。

    delay/delta(x, n) 需要 n 日前的值（额外 n 日）；ts_*(x, n) 的窗口含当日，额外 n−1 日。

    Args:
        node: 语法树节点。

    Returns:
        额外交易日数。
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _SERIES_FUNCTIONS
    ):
        n = int(node.args[1].value)  # type: ignore[attr-defined]
        extra = n if node.func.id in ("delay", "delta") else n - 1
        return extra + _series_window(node.args[0])
    return max((_series_window(child) for child in ast.iter_child_nodes(node)), default=0)


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
            if node.func.id in _SERIES_FUNCTIONS:
                # 时序函数只能按数组求值（evaluate_expression_series），标量路径视为缺失
                raise ExpressionError("时序函数需要按交易日序列求值")
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


def evaluate_expression_series(
    parsed: ParsedExpression, lookup: Callable[[str], np.ndarray]
) -> np.ndarray:
    """按 交易日 × 指数 数组求表达式（含时序函数时使用）。

    缺失值用 NaN 表示并向上传播，与标量求值的"任一缺失即缺失"一致；除零、定义域错误
    与非有限结果同样记为 NaN。时序函数沿第 0 轴（交易日，升序）对每个指数独立计算。

    Args:
        parsed: 解析结果。
        lookup: 因子引用 → 形如 (交易日数, 指数数) 的浮点数组（缺失为 NaN）。

    Returns:
        同形数组；非有限值已置为 NaN。
    """

    def _eval(node: ast.AST) -> np.ndarray | float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return np.asarray(lookup(node.id), dtype=float)
        if isinstance(node, ast.BinOp):
            left, right = _eval(node.left), _eval(node.right)
            op = type(node.op)
            if op is ast.Add:
                return np.add(left, right)
            if op is ast.Sub:
                return np.subtract(left, right)
            if op is ast.Mult:
                return np.multiply(left, right)
            if op is ast.Div:
                return np.divide(left, right)
            return np.power(left, right)
        if isinstance(node, ast.UnaryOp):
            value = _eval(node.operand)
            return np.negative(value) if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in _SERIES_FUNCTIONS:
                base = np.asarray(_eval(node.args[0]), dtype=float)
                return _apply_series(name, base, int(node.args[1].value))  # type: ignore[attr-defined]
            args = [_eval(arg) for arg in node.args]
            if name == "abs":
                return np.abs(args[0])
            if name == "min":
                return _reduce(np.minimum, args)
            if name == "max":
                return _reduce(np.maximum, args)
            if name == "log":
                return np.log(args[0])
            if name == "sqrt":
                return np.sqrt(args[0])
            return np.sign(args[0])
        raise ExpressionError(f"不支持的语法 {type(node).__name__}")

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        result = np.array(_eval(parsed.tree), dtype=float)
    result[~np.isfinite(result)] = np.nan
    return result


def _reduce(fn: Callable[..., np.ndarray], args: list[np.ndarray | float]) -> np.ndarray:
    """对多个参数逐个两两规约（NaN 传播）。"""
    value = args[0]
    for arg in args[1:]:
        value = fn(value, arg)
    return np.asarray(value)


def _apply_series(name: str, values: np.ndarray, n: int) -> np.ndarray:
    """沿交易日轴计算时序函数（严格口径：窗口内任一缺失即缺失）。

    Args:
        name: 时序函数名。
        values: 形如 (交易日数, 指数数) 的数组。
        n: 窗口。

    Returns:
        同形数组；窗口不足的前若干行为 NaN。
    """
    values = np.atleast_2d(values)
    out = np.full_like(values, np.nan, dtype=float)
    rows = values.shape[0]
    if name in ("delay", "delta"):
        if rows > n:
            lagged = values[:-n]
            out[n:] = lagged if name == "delay" else values[n:] - lagged
        return out
    if rows < n:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, n, axis=0)
    # windows 形如 (rows-n+1, 指数数, n)；NaN 自然传播到均值 / 标准差
    if name == "ts_mean":
        out[n - 1 :] = windows.mean(axis=-1)
    elif name == "ts_std":
        out[n - 1 :] = windows.std(axis=-1)
    elif name == "ts_zscore":
        std = windows.std(axis=-1)
        out[n - 1 :] = (values[n - 1 :] - windows.mean(axis=-1)) / std
    else:
        current = values[n - 1 :][..., None]
        below = (windows < current).sum(axis=-1)
        ties = (windows == current).sum(axis=-1) - 1
        rank = (below + 0.5 * ties) / (n - 1)
        out[n - 1 :] = np.where(np.isnan(windows).any(axis=-1), np.nan, rank)
    return out


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
