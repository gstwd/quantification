"""派生因子时序函数测试：解析与窗口、数组求值、compute_matrix 扩展轴与实例回望。"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue, period_lookback_days
from quant_etf_api.factors.builtins.derived import ExpressionComputer
from quant_etf_api.factors.catalog import FactorInstance, build_default_registry
from quant_etf_api.factors.compute import FactorComputeService
from quant_etf_api.factors.expression import (
    ExpressionError,
    evaluate_expression,
    evaluate_expression_series,
    expression_fingerprint,
    parse_expression,
)
from quant_etf_api.factors.templates import build_template

_DAYS = [date(2025, 3, 3) + timedelta(days=i) for i in range(10)]


class TestParse:
    """时序函数的解析与窗口推导。"""

    @pytest.mark.parametrize(
        ("text", "window"),
        [
            ("delay(x, 3)", 3),
            ("delta(x, 5)", 5),
            ("ts_mean(x, 20)", 19),
            ("ts_mean(delta(x, 5), 20)", 24),
            ("ts_zscore(x, 10) + delay(y, 30)", 30),
            ("x - y", 0),
        ],
    )
    def test_series_window(self, text: str, window: int) -> None:
        """嵌套窗口累加、并列取最大。"""
        assert parse_expression(text).series_window == window

    @pytest.mark.parametrize(
        "text",
        [
            "delay(x)",
            "delay(x, y)",
            "delay(x, 2.5)",
            "delay(x, True)",
            "ts_std(x, 1)",
            "ts_mean(x, 251)",
            "delay(x, 0)",
            "ts_rank(x, -3)",
            "delay + x",
            "ts_mean(ts_mean(x, 200), 100)",
        ],
    )
    def test_rejected(self, text: str) -> None:
        """窗口非字面量 / 越界 / 参数个数错 / 函数名作引用 / 嵌套总窗口超限均拒绝。"""
        with pytest.raises(ExpressionError):
            parse_expression(text)

    def test_refs_exclude_window_constant(self) -> None:
        """窗口常量不是引用；引用按出现顺序。"""
        assert parse_expression("ts_mean(a, 5) / delay(b, 2)").refs == ("a", "b")

    def test_scalar_path_returns_none(self) -> None:
        """时序表达式不能走标量求值（视为缺失而不是抛错）。"""
        parsed = parse_expression("delay(x, 1)")
        assert evaluate_expression(parsed, lambda _ref: 1.0) is None

    def test_fingerprint_distinguishes_window(self) -> None:
        """窗口不同即不同的派生因子（需要各自的诊断）。"""
        deps = {"x": "volatility:{}"}
        a = expression_fingerprint(parse_expression("ts_mean(x, 20)").text, deps)
        b = expression_fingerprint(parse_expression("ts_mean(x, 21)").text, deps)
        assert a != b


def _series(values: list[float]) -> np.ndarray:
    """单列数组。"""
    return np.array(values, dtype=float)[:, None]


class TestSeriesEvaluation:
    """数组求值的逐函数语义。"""

    def _eval(self, text: str, **refs: list[float]) -> list[float]:
        parsed = parse_expression(text)
        arrays = {name: _series(values) for name, values in refs.items()}
        out = evaluate_expression_series(parsed, lambda ref: arrays[ref])
        return [None if np.isnan(v) else round(float(v), 6) for v in out[:, 0]]

    def test_delay_and_delta(self) -> None:
        """delay 取 n 日前；delta 为差。"""
        assert self._eval("delay(x, 2)", x=[1, 2, 3, 4]) == [None, None, 1.0, 2.0]
        assert self._eval("delta(x, 1)", x=[1, 3, 6, 10]) == [None, 2.0, 3.0, 4.0]

    def test_mean_std_zscore(self) -> None:
        """滚动均值 / 总体标准差 / z 分数。"""
        assert self._eval("ts_mean(x, 3)", x=[1, 2, 3, 4]) == [None, None, 2.0, 3.0]
        std = self._eval("ts_std(x, 2)", x=[1, 3, 3])
        assert std == [None, 1.0, 0.0]
        z = self._eval("ts_zscore(x, 2)", x=[1, 3, 3])
        # 第 3 行标准差为 0 → 除零记为缺失
        assert z == [None, 1.0, None]

    def test_rank_with_ties(self) -> None:
        """ts_rank 为窗口内百分位（并列取一半）。"""
        assert self._eval("ts_rank(x, 3)", x=[1, 2, 3, 3, 1]) == [None, None, 1.0, 0.75, 0.0]

    def test_missing_is_strict(self) -> None:
        """窗口内任一缺失即缺失；缺失经四则运算传播。"""
        nan = float("nan")
        assert self._eval("ts_mean(x, 2)", x=[1, nan, 3, 5]) == [None, None, None, 4.0]
        assert self._eval("ts_rank(x, 2)", x=[nan, 1, 2]) == [None, None, 1.0]
        assert self._eval("delay(x, 1) + y", x=[1, 2, 3], y=[nan, 1, 1]) == [None, 2.0, 3.0]

    def test_math_errors_become_missing(self) -> None:
        """log 非正、除零与非有限值为缺失。"""
        assert self._eval("log(delta(x, 1))", x=[1, 1, 3]) == [None, None, round(np.log(2), 6)]
        assert self._eval("x / delay(x, 1)", x=[0, 2, 4]) == [None, None, 2.0]


class _DatedComputer:
    """测试用计算器：值 = 日期序号 × 系数（逐点计算）。"""

    def __init__(self, scale: float) -> None:
        self._scale = scale

    @property
    def spec(self) -> FactorSpec:
        return FactorSpec(
            factor_id="dated",
            name="dated",
            category="test",
            version="1",
            description="",
            required_data=[],
            lookback_days=30,
        )

    def compute(self, index_code: str, trade_date: date, ctx: Any) -> FactorValue:
        return FactorValue(factor_id="dated", numeric=_DAYS.index(trade_date) * self._scale)


def _dated_instance() -> FactorInstance:
    """构造逐日变化的依赖实例（回望 30 日）。"""
    template = build_template(
        "t_dated", lambda _p: _DatedComputer(1.0), name="dated", compute_lookback_days=lambda _p: 30
    )
    return FactorInstance(
        instance_id="x", label="x", template=template, params={}, computer=_DatedComputer(1.0)
    )


def _series_instance(expr: str) -> FactorInstance:
    """构造依赖 x 的派生实例。"""
    computer = ExpressionComputer(expr)
    computer.bind_dependencies({"x": _dated_instance()})
    template = build_default_registry().get("expr")
    assert template is not None
    return FactorInstance(
        instance_id="s", label=expr, template=template, params={"expr": expr}, computer=computer
    )


class TestComputeMatrix:
    """compute_matrix 在扩展轴上求时序派生因子，只返回目标日。"""

    def test_uses_calculation_axis_for_warmup(self) -> None:
        """回测口径：计算轴含预热日，目标日首日即有值。"""
        service = FactorComputeService.__new__(FactorComputeService)
        matrix = service.compute_matrix(
            [_series_instance("delta(x, 3)")],
            ["A"],
            _DAYS[5:7],
            ctx=FactorContext(),
            calculation_dates=_DAYS,
        )
        assert sorted(matrix.values) == _DAYS[5:7]
        assert matrix.values[_DAYS[5]][("A", "s")] == 3.0
        assert matrix.values[_DAYS[6]][("A", "x")] == 6.0

    def test_uses_context_bars_without_axis(self) -> None:
        """实时 / 诊断口径：没有计算轴时按上下文行情日期向前扩展。"""
        ctx = FactorContext(index_bars={("A", day): SimpleNamespace() for day in _DAYS})
        service = FactorComputeService.__new__(FactorComputeService)
        matrix = service.compute_matrix(
            [_series_instance("ts_mean(x, 4)")], ["A"], [_DAYS[-1]], ctx=ctx
        )
        assert list(matrix.values) == [_DAYS[-1]]
        assert matrix.values[_DAYS[-1]][("A", "s")] == pytest.approx(7.5)

    def test_sparse_targets_step_on_full_axis(self) -> None:
        """目标日稀疏时，时序函数仍沿完整交易日轴步进（不是按目标日步进）。"""
        service = FactorComputeService.__new__(FactorComputeService)
        matrix = service.compute_matrix(
            [_series_instance("delay(x, 1)")],
            ["A"],
            [_DAYS[4], _DAYS[8]],
            ctx=FactorContext(),
            calculation_dates=_DAYS,
        )
        assert matrix.values[_DAYS[8]][("A", "s")] == 7.0

    def test_instance_lookback_adds_window(self) -> None:
        """派生实例回望 = 依赖回望 + 时序窗口折算的自然日。"""
        instance = _series_instance("ts_mean(delta(x, 5), 20)")
        assert instance.lookback_days == 30 + period_lookback_days(24)
        assert _series_instance("x * 2").lookback_days == 30
