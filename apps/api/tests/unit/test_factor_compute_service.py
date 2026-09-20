"""因子现算编排单元测试。

覆盖矩阵计算的键规范、同参实例复用、计算失败记录、目标日期过滤、
市场级模板的全市场行情加载，以及逐点计算的时点化上下文。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from quant_etf_api.engine.config import FactorAliasConfig
from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue
from quant_etf_api.factors.catalog import (
    FactorTemplateRegistry,
    build_default_registry,
    build_template,
)
from quant_etf_api.factors.compute import FactorComputeService, FactorMatrix
from quant_etf_api.factors.templates import ParameterSpec

DATES = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
CODES = ["000300", "000905"]


@dataclass
class _Bar:
    """模拟日线行。"""

    close_price: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    volume: float | None = None
    change_pct: float | None = None


@dataclass
class _Valuation:
    """模拟估值行。"""

    pe: float | None = None
    pb: float | None = None
    pe_percentile: float | None = None
    pb_percentile: float | None = None


def _bars() -> dict[tuple[str, date], _Bar]:
    """构造覆盖两个指数的三日行情。"""
    bars: dict[tuple[str, date], _Bar] = {}
    for code_index, code in enumerate(CODES):
        for day_index, trade_date in enumerate(DATES):
            close = 100.0 + code_index * 10 + day_index
            bars[(code, trade_date)] = _Bar(
                close_price=close,
                open_price=close,
                high_price=close + 1,
                low_price=close - 1,
                volume=1000.0,
                change_pct=0.1,
            )
    return bars


class _CountingComputer:
    """记录调用次数的桩计算器。"""

    calls = 0

    def __init__(self, factor_id: str = "close_price") -> None:
        self._factor_id = factor_id

    @property
    def spec(self) -> FactorSpec:
        """返回因子元数据。"""
        return FactorSpec(
            factor_id=self._factor_id,
            name="计数桩",
            category="price",
            version="1.0.0",
            description="计数桩",
            required_data=["index_bars"],
            lookback_days=1,
        )

    def compute(
        self, index_code: str, trade_date: date, ctx: FactorContext
    ) -> FactorValue:
        """累加调用次数并返回当日收盘价。"""
        type(self).calls += 1
        bar = ctx.index_bars.get((index_code, trade_date))
        return FactorValue(
            factor_id=self._factor_id,
            numeric=None if bar is None else bar.close_price,
        )


class _ExplodingComputer(_CountingComputer):
    """始终抛异常的计算器。"""

    def compute(
        self, index_code: str, trade_date: date, ctx: FactorContext
    ) -> FactorValue:
        raise RuntimeError("boom")


def _patch_repos(monkeypatch: pytest.MonkeyPatch, valuation: dict | None = None) -> None:
    """把现算服务依赖的三个仓库替换为内存实现。"""
    bars = _bars()
    valuation_rows = valuation or {}

    class _BarRepo:
        def __init__(self, _db: Any) -> None:
            pass

        def find_all_date_range(
            self, start: date, end: date, index_codes: list[str] | None = None
        ) -> dict:
            return bars

        def find_trading_dates(
            self, start: date, end: date, index_codes: list[str]
        ) -> list[date]:
            return DATES

        def get_latest_trade_date(self) -> date:
            return DATES[-1]

    class _ValuationRepo:
        def __init__(self, _db: Any) -> None:
            pass

        def find_range(
            self, start: date, end: date, index_codes: list[str] | None = None
        ) -> dict:
            return dict(valuation_rows)

    class _MacroRepo:
        def __init__(self, _db: Any) -> None:
            pass

        def find_by_codes(self, codes: list[str]) -> list[Any]:
            return []

    monkeypatch.setattr("quant_etf_api.factors.compute.IndexDailyBarRepository", _BarRepo)
    monkeypatch.setattr("quant_etf_api.factors.compute.IndexValuationRepository", _ValuationRepo)
    monkeypatch.setattr("quant_etf_api.factors.compute.MacroIndicatorRepository", _MacroRepo)


def _service(registry: FactorTemplateRegistry | None = None) -> FactorComputeService:
    """构造使用桩数据库会话的现算服务。"""
    return FactorComputeService(db=object(), registry=registry or build_default_registry())  # type: ignore[arg-type]


class TestFactorMatrix:
    """矩阵容器行为。"""

    def test_day_returns_empty_for_unknown_date(self) -> None:
        """未计算的日期返回空字典，不抛错。"""
        matrix = FactorMatrix(values={DATES[0]: {("000300", "close_price"): 1.0}})
        assert matrix.day(DATES[0]) == {("000300", "close_price"): 1.0}
        assert matrix.day(DATES[1]) == {}


class TestComputeMatrix:
    """矩阵计算核心行为。"""

    def test_keys_use_instance_id(self) -> None:
        """矩阵键的第二维是实例 ID（等同于策略中的引用名）。"""
        service = _service()
        instance = service.resolve("close_price", {})
        ctx = FactorContext(index_bars=_bars())
        matrix = service.compute_matrix([instance], CODES, DATES, ctx=ctx)

        assert set(matrix.day(DATES[0])) == {
            ("000300", "close_price"),
            ("000905", "close_price"),
        }
        assert matrix.day(DATES[0])[("000300", "close_price")] == 100.0

    def test_missing_bar_yields_none(self) -> None:
        """行情缺失的资产取值为 None，而不是被跳过。"""
        service = _service()
        instance = service.resolve("close_price", {})
        bars = _bars()
        del bars[("000905", DATES[1])]
        matrix = service.compute_matrix(
            [instance], CODES, DATES, ctx=FactorContext(index_bars=bars)
        )
        assert matrix.day(DATES[1])[("000905", "close_price")] is None

    def test_results_limited_to_target_dates(self) -> None:
        """计算轴可超出目标日期，结果只保留目标日期。"""
        service = _service()
        instance = service.resolve("close_price", {})
        ctx = FactorContext(index_bars=_bars())
        matrix = service.compute_matrix(
            [instance],
            CODES,
            [DATES[0]],
            ctx=ctx,
            calculation_dates=DATES,
        )
        assert set(matrix.values) == {DATES[0]}

    def test_same_template_params_share_computer(self) -> None:
        """同模板同参数的别名只创建一个计算器、只算一次。"""
        _CountingComputer.calls = 0
        registry = FactorTemplateRegistry()
        registry.register(
            build_template(
                "sma",
                lambda _params: _CountingComputer("stub_ma"),
                parameter_schema={"period": ParameterSpec("integer", 2, 250, 10, "周期")},
                compute_lookback_days=lambda _params: 1,
            )
        )
        aliases = {
            "fast_a": FactorAliasConfig(template_id="sma", params={"period": 10}),
            "fast_b": FactorAliasConfig(template_id="sma", params={"period": 10}),
        }
        instances = list(registry.resolve_all(["fast_a", "fast_b"], aliases).values())
        service = FactorComputeService(db=object(), registry=registry)  # type: ignore[arg-type]

        matrix = service.compute_matrix(
            instances, [CODES[0]], [DATES[0]], ctx=FactorContext(index_bars=_bars())
        )

        # 1 个日期 × 1 个指数 = 1 次调用，两个别名共享同一次结果
        assert _CountingComputer.calls == 1
        assert (
            matrix.day(DATES[0])[("000300", "fast_a")]
            == matrix.day(DATES[0])[("000300", "fast_b")]
        )

    def test_duplicate_instance_id_with_different_params_is_rejected(self) -> None:
        """同模板不同参数直接用模板 ID 作实例 ID 时快速失败，不静默覆盖。"""
        registry = build_default_registry()
        service = FactorComputeService(db=object(), registry=registry)  # type: ignore[arg-type]
        instances = [
            registry.resolve_params("return", {"period": 5}),
            registry.resolve_params("return", {"period": 20}),
        ]

        with pytest.raises(ValueError, match="对应了多个参数组合"):
            service.compute_matrix(
                instances, [CODES[0]], [DATES[0]], ctx=FactorContext(index_bars=_bars())
            )

    def test_failed_instance_is_recorded(self) -> None:
        """计算抛异常的实例记入 failed，不产出数值。"""
        registry = FactorTemplateRegistry()
        registry.register(build_template("boom", lambda _params: _ExplodingComputer("boom")))
        service = FactorComputeService(db=object(), registry=registry)  # type: ignore[arg-type]
        instance = registry.resolve("boom")

        matrix = service.compute_matrix(
            [instance], [CODES[0]], [DATES[0]], ctx=FactorContext(index_bars=_bars())
        )

        assert matrix.failed == {"boom"}
        assert matrix.day(DATES[0]).get((CODES[0], "boom")) is None

    def test_empty_inputs_return_empty_matrix(self) -> None:
        """缺少实例、指数或日期时不计算。"""
        service = _service()
        instance = service.resolve("close_price", {})
        assert service.compute_matrix([], CODES, DATES).values == {}
        assert service.compute_matrix([instance], [], DATES).values == {}
        assert service.compute_matrix([instance], CODES, []).values == {}


class TestBuildContext:
    """上下文装配行为。"""

    def test_market_scope_loads_all_active_indexes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """引用市场级模板时日线扩展到全部活跃指数。"""
        requested: list[list[str] | None] = []

        class _BarRepo:
            def __init__(self, _db: Any) -> None:
                pass

            def find_all_date_range(
                self, start: date, end: date, index_codes: list[str] | None = None
            ) -> dict:
                requested.append(index_codes)
                return {}

        class _ValuationRepo:
            def __init__(self, _db: Any) -> None:
                pass

            def find_range(
                self, start: date, end: date, index_codes: list[str] | None = None
            ) -> dict:
                return {}

        class _MacroRepo:
            def __init__(self, _db: Any) -> None:
                pass

            def find_by_codes(self, codes: list[str]) -> list[Any]:
                return []

        monkeypatch.setattr("quant_etf_api.factors.compute.IndexDailyBarRepository", _BarRepo)
        monkeypatch.setattr(
            "quant_etf_api.factors.compute.IndexValuationRepository", _ValuationRepo
        )
        monkeypatch.setattr("quant_etf_api.factors.compute.MacroIndicatorRepository", _MacroRepo)

        service = _service()
        monkeypatch.setattr(service, "active_index_codes", lambda: ["000300", "399001"])
        breadth = service.resolve("breadth_ma_pct", {})

        service.build_context([breadth], ["000300"], [DATES[0]])

        assert requested == [["000300", "399001"]]

    def test_scope_only_without_market_template(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无市场级模板时只加载策略资产范围的行情。"""
        requested: list[list[str] | None] = []

        class _BarRepo:
            def __init__(self, _db: Any) -> None:
                pass

            def find_all_date_range(
                self, start: date, end: date, index_codes: list[str] | None = None
            ) -> dict:
                requested.append(index_codes)
                return {}

        class _ValuationRepo:
            def __init__(self, _db: Any) -> None:
                pass

            def find_range(
                self, start: date, end: date, index_codes: list[str] | None = None
            ) -> dict:
                return {}

        class _MacroRepo:
            def __init__(self, _db: Any) -> None:
                pass

            def find_by_codes(self, codes: list[str]) -> list[Any]:
                return []

        monkeypatch.setattr("quant_etf_api.factors.compute.IndexDailyBarRepository", _BarRepo)
        monkeypatch.setattr(
            "quant_etf_api.factors.compute.IndexValuationRepository", _ValuationRepo
        )
        monkeypatch.setattr("quant_etf_api.factors.compute.MacroIndicatorRepository", _MacroRepo)

        service = _service()
        close = service.resolve("close_price", {})
        service.build_context([close], ["000300"], [DATES[0]])

        assert requested == [["000300"]]

    def test_point_computer_sees_as_of_valuation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """逐点计算的估值视图按目标日期时点化，不用未来数据。"""
        valuation = {
            ("000300", DATES[0]): _Valuation(pe=10.0, pe_percentile=20.0),
            ("000300", DATES[1]): _Valuation(pe=10.0, pe_percentile=80.0),
        }
        _patch_repos(monkeypatch, valuation)
        service = _service()
        instance = service.resolve("pe_percentile", {})

        matrix = service.compute_matrix([instance], ["000300"], [DATES[0], DATES[1]])

        assert matrix.day(DATES[0])[("000300", "pe_percentile")] == 20.0
        assert matrix.day(DATES[1])[("000300", "pe_percentile")] == 80.0

    def test_market_factors_take_first_proxy_with_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """市场因子按代理指数顺序取第一个有值的口径。"""
        valuation = {
            ("000905", DATES[0]): _Valuation(pe_percentile=45.0),
        }
        _patch_repos(monkeypatch, valuation)
        service = _service()
        instance = service.resolve("pe_percentile", {})

        # 第一个代理指数 000300 无估值数据，回退到 000905
        result = service.compute_market_factors([instance], DATES[0], ["000300", "000905"])

        assert result == {"pe_percentile": 45.0}

    def test_market_factors_none_when_no_proxy_has_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """所有代理指数都无值时返回 None，而不是静默省略。"""
        _patch_repos(monkeypatch)
        service = _service()
        instance = service.resolve("pe_percentile", {})

        result = service.compute_market_factors([instance], DATES[0], ["000300", "000905"])

        assert result == {"pe_percentile": None}
