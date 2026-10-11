"""因子层口径修复与性能优化回归测试。

覆盖：
- 宏观指标发布滞后（PMI/CPI 前视偏差）；
- ERP 百分位使用历史当日 LPR 且窗口固定为 730 天；
- Donchian 通道不含当日、RSI 无涨跌为 50、月线连阳不足为 None、量比完整窗口；
- 全部内置模板实现批量协议、逐点与批量一致、追加未来数据不改变历史值（PIT）；
- 横截面评分逐因子标准化、平均秩并列；
- 市场级模板只计算一次并广播；
- 复合因子面板轴覆盖整个请求区间；
- 行业选择缓存随数据版本失效、不固化未同步日期的缺失。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import ScoreConfig
from quant_etf_api.engine.score import CrossSectionScorer
from quant_etf_api.factors.base import BatchFactorComputer, FactorContext
from quant_etf_api.factors.builtins.erp import ERPPercentileComputer
from quant_etf_api.factors.builtins.macro import PMIMomentumComputer
from quant_etf_api.factors.builtins.monthly import MonthlyStreakComputer
from quant_etf_api.factors.builtins.technical import DonchianHighComputer, RSIComputer
from quant_etf_api.factors.builtins.volume import AmountRatioComputer, VolumeRatioComputer
from quant_etf_api.factors.catalog import OHLC_DEPENDENT_TEMPLATE_IDS, build_default_registry
from quant_etf_api.factors.compute import FactorComputeService
from quant_etf_api.factors.macro_period import (
    latest_value_as_of,
    macro_available_date,
    macro_indicators_as_of,
)
from quant_etf_api.factors.normalization import normalize_rank, normalize_zscore
from quant_etf_api.services import index_factor_panel_service as panel_module
from quant_etf_api.services.index_factor_panel_service import IndexFactorPanelService

# 复合面板类模板依赖 DB 装配的面板，单独覆盖，不进入通用一致性测试
_PANEL_TEMPLATES = {"index_diffusion_ratio", "rrg_industry_match_score"}


@dataclass
class _Bar:
    """模拟日线行。"""

    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float | None
    volume: float | None = None
    turnover: float | None = None
    change_pct: float | None = None


@dataclass
class _Valuation:
    """模拟估值行。"""

    pe: float | None
    pb: float | None
    pe_percentile: float | None
    pb_percentile: float | None


def _synthetic_bars(
    code: str, start: date, days: int, phase: float = 0.0
) -> dict[tuple[str, date], _Bar]:
    """构造逐自然日的完整 OHLCV 行情（收盘价含趋势与周期波动）。"""
    bars: dict[tuple[str, date], _Bar] = {}
    previous = None
    for k in range(days):
        trade_date = start + timedelta(days=k)
        close = 100.0 + 15.0 * math.sin(k / 37.0 + phase) + 0.03 * k + 2.0 * math.sin(k / 3.1)
        open_price = close - 0.6 * math.sin(k / 4.0 + phase)
        bars[(code, trade_date)] = _Bar(
            open_price=open_price,
            high_price=max(open_price, close) + 1.0 + abs(math.cos(k / 7.0)),
            low_price=min(open_price, close) - 1.0 - abs(math.cos(k / 11.0)),
            close_price=close,
            volume=1000.0 + 300.0 * math.sin(k / 5.0 + phase),
            turnover=5000.0 + 800.0 * math.cos(k / 6.0 + phase),
            change_pct=None if previous is None else (close / previous - 1) * 100,
        )
        previous = close
    return bars


def _synthetic_valuation(code: str, start: date, days: int) -> dict[tuple[str, date], _Valuation]:
    """构造逐日估值行。"""
    return {
        (code, start + timedelta(days=k)): _Valuation(
            pe=12.0 + 3.0 * math.sin(k / 50.0),
            pb=1.5 + 0.3 * math.cos(k / 40.0),
            pe_percentile=50.0 + 40.0 * math.sin(k / 60.0),
            pb_percentile=50.0 + 40.0 * math.cos(k / 60.0),
        )
        for k in range(days)
    }


def _synthetic_macro(start: date, days: int) -> dict[str, dict[str, float]]:
    """构造月度 PMI 与每月 20 日报价的 LPR。"""
    pmi: dict[str, float] = {}
    lpr: dict[str, float] = {}
    cursor = date(start.year, start.month, 1)
    end = start + timedelta(days=days)
    index = 0
    while cursor <= end:
        pmi[cursor.isoformat()] = 49.0 + 1.5 * math.sin(index / 3.0)
        lpr[date(cursor.year, cursor.month, 20).isoformat()] = 4.0 - 0.05 * index
        index += 1
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return {"pmi": pmi, "lpr1y": lpr}


START = date(2021, 1, 1)
DAYS = 1100
TARGET_DATES = [START + timedelta(days=d) for d in (800, 870, 935, 990)]


def _full_ctx(cutoff: date | None = None) -> FactorContext:
    """构造双指数完整上下文；cutoff 给定时只保留截止该日的数据。"""
    bars = {**_synthetic_bars("000300", START, DAYS), **_synthetic_bars("000905", START, DAYS, 1.3)}
    valuation = _synthetic_valuation("000300", START, DAYS)
    macro = _synthetic_macro(START, DAYS)
    if cutoff is not None:
        bars = {key: value for key, value in bars.items() if key[1] <= cutoff}
        valuation = {key: value for key, value in valuation.items() if key[1] <= cutoff}
    return FactorContext(index_bars=bars, index_valuation=valuation, macro_indicators=macro)


# ── 宏观发布滞后 ─────────────────────────────────────────────────────────


class TestMacroPublicationLag:
    """月度宏观指标只在发布后可见。"""

    def test_pmi_visible_from_next_month(self) -> None:
        """9 月 PMI 于 10 月 1 日起可见，9 月 30 日不可见。"""
        assert macro_available_date("pmi", "2024-09-01") == date(2024, 10, 1)
        assert macro_available_date("pmi", "2024-12") == date(2025, 1, 1)

    def test_cpi_visible_mid_next_month(self) -> None:
        """CPI 次月 15 日起可见；LPR 报价日当日可见。"""
        assert macro_available_date("cpi", "2024-09-01") == date(2024, 10, 15)
        assert macro_available_date("lpr1y", "2024-09-20") == date(2024, 9, 20)

    def test_as_of_view_excludes_unpublished_month(self) -> None:
        """时点化视图不包含参考月当月尚未公布的 PMI。"""
        view = macro_indicators_as_of(
            {"pmi": {"2024-08": 49.1, "2024-09": 49.8}}, date(2024, 9, 30)
        )
        assert view == {"pmi": {"2024-08": 49.1}}

    def test_latest_value_with_indicator_code(self) -> None:
        """带指标代码时最近一期按可见日判定。"""
        values = {"2024-08-01": 49.1, "2024-09-01": 49.8}
        assert latest_value_as_of(values, date(2024, 9, 30), "pmi") == ("2024-08-01", 49.1)
        assert latest_value_as_of(values, date(2024, 10, 1), "pmi") == ("2024-09-01", 49.8)

    def test_pmi_momentum_ignores_current_reference_month(self) -> None:
        """参考月当月计算动量时只能用上月及更早已公布的 PMI。"""
        ctx = FactorContext(
            macro_indicators={
                "pmi": {
                    "2024-05-01": 49.0,
                    "2024-06-01": 49.5,
                    "2024-07-01": 50.0,
                    "2024-08-01": 50.5,
                    "2024-09-01": 55.0,
                }
            }
        )
        computer = PMIMomentumComputer(months=2)
        september = computer.compute("000300", date(2024, 9, 20), ctx)
        october = computer.compute("000300", date(2024, 10, 8), ctx)
        assert september.payload["latest_period"] == "2024-08-01"
        assert september.numeric == pytest.approx(1.0)
        assert october.payload["latest_period"] == "2024-09-01"


# ── ERP 百分位 ───────────────────────────────────────────────────────────


class TestERPPercentile:
    """ERP 百分位使用历史当日 LPR 与固定 2 年窗口。"""

    @staticmethod
    def _ctx(lpr: dict[str, float], extra_old: bool = False) -> FactorContext:
        trade_date = date(2024, 6, 28)
        valuation: dict[tuple[str, date], Any] = {}
        for k in range(400):
            day = trade_date - timedelta(days=k)
            valuation[("000300", day)] = _Valuation(
                pe=10.0 + ((k + 3) % 7), pb=None, pe_percentile=None, pb_percentile=None
            )
        if extra_old:
            for k in range(800, 1000):
                day = trade_date - timedelta(days=k)
                valuation[("000300", day)] = _Valuation(
                    pe=1.0, pb=None, pe_percentile=None, pb_percentile=None
                )
        return FactorContext(index_valuation=valuation, macro_indicators={"lpr1y": lpr})

    def test_historical_lpr_changes_percentile(self) -> None:
        """历史 LPR 不同会改变百分位（旧实现套用当前 LPR，利率不起作用）。"""
        trade_date = date(2024, 6, 28)
        flat = self._ctx({"2019-08-20": 3.45})
        stepped = self._ctx({"2019-08-20": 5.0, "2024-03-20": 3.45})
        computer = ERPPercentileComputer()
        flat_value = computer.compute("000300", trade_date, flat).numeric
        stepped_value = computer.compute("000300", trade_date, stepped).numeric
        assert flat_value is not None and stepped_value is not None
        assert flat_value != stepped_value

    def test_window_ignores_data_older_than_two_years(self) -> None:
        """730 天之前的估值不进入历史分布（实时/回测加载范围不同也不影响结果）。"""
        trade_date = date(2024, 6, 28)
        lpr = {"2019-08-20": 3.45}
        computer = ERPPercentileComputer()
        base = computer.compute("000300", trade_date, self._ctx(lpr))
        with_old = computer.compute("000300", trade_date, self._ctx(lpr, extra_old=True))
        assert base.numeric == with_old.numeric
        assert base.payload["history_count"] == with_old.payload["history_count"]


# ── 单因子口径 ───────────────────────────────────────────────────────────


class TestSingleFactorSemantics:
    """单个计算器的口径修复。"""

    def test_donchian_breakout_can_fire(self) -> None:
        """上涨序列中收盘价可以高于不含当日的通道上轨。"""
        bars = _synthetic_bars("000300", START, 60)
        trade_date = START + timedelta(days=59)
        for k in range(60):
            day = START + timedelta(days=k)
            close = 100.0 + k
            bars[("000300", day)] = _Bar(close, close + 0.5, close - 0.5, close)
        ctx = FactorContext(index_bars=bars)
        upper = DonchianHighComputer(period=20).compute("000300", trade_date, ctx)
        assert upper.numeric == pytest.approx(100.0 + 58 + 0.5)
        assert bars[("000300", trade_date)].close_price > upper.numeric

    def test_rsi_flat_market_is_neutral(self) -> None:
        """窗口内无涨跌时 RSI 为 50。"""
        bars = {
            ("000300", START + timedelta(days=k)): _Bar(10.0, 10.0, 10.0, 10.0) for k in range(20)
        }
        result = RSIComputer(14).compute("000300", START + timedelta(days=19), FactorContext(bars))
        assert result.numeric == 50.0

    def test_monthly_streak_insufficient_is_none(self) -> None:
        """月线不足 2 个月时为 None，而不是与收平混淆的 0。"""
        bars = _synthetic_bars("000300", date(2024, 3, 1), 20)
        result = MonthlyStreakComputer().compute("000300", date(2024, 3, 20), FactorContext(bars))
        assert result.numeric is None

    @pytest.mark.parametrize("computer_cls", [VolumeRatioComputer, AmountRatioComputer])
    def test_ratio_requires_full_window(self, computer_cls: type) -> None:
        """历史不足 N 个交易日时量比为 None，不用不完整窗口出值。"""
        bars = _synthetic_bars("000300", START, 10)
        ctx = FactorContext(index_bars=bars)
        trade_date = START + timedelta(days=9)
        assert computer_cls(period=20).compute("000300", trade_date, ctx).numeric is None
        assert computer_cls(period=9).compute("000300", trade_date, ctx).numeric is not None


# ── 全模板一致性 ─────────────────────────────────────────────────────────


_TEMPLATE_IDS = [
    template_id
    for template_id in build_default_registry().ids()
    if template_id not in _PANEL_TEMPLATES
]


class TestAllTemplatesConsistency:
    """全部内置模板的批量协议、逐点/批量一致性与时点安全。"""

    def test_all_builtin_templates_implement_batch(self) -> None:
        """内置模板全部实现批量协议，现算不再走逐日时点化上下文。"""
        registry = build_default_registry()
        for template in registry.all():
            assert isinstance(template.build_computer(), BatchFactorComputer), template.template_id

    @pytest.mark.parametrize("template_id", _TEMPLATE_IDS)
    def test_point_matches_batch(self, template_id: str) -> None:
        """逐点计算与批量计算结果一致。"""
        computer = build_default_registry().resolve_params(template_id).computer
        ctx = _full_ctx()
        batch = computer.compute_batch("000300", TARGET_DATES, ctx)
        for trade_date in TARGET_DATES:
            point = computer.compute("000300", trade_date, ctx)
            assert point.numeric == batch[trade_date].numeric, (template_id, trade_date)

    @pytest.mark.parametrize("template_id", _TEMPLATE_IDS)
    def test_future_data_does_not_change_history(self, template_id: str) -> None:
        """上下文中追加目标日之后的数据不改变目标日因子值（无前视）。"""
        computer = build_default_registry().resolve_params(template_id).computer
        values = []
        for trade_date in TARGET_DATES:
            truncated = computer.compute("000300", trade_date, _full_ctx(cutoff=trade_date))
            full = computer.compute_batch("000300", [trade_date], _full_ctx())[trade_date]
            values.append(full.numeric)
            assert truncated.numeric == full.numeric, (template_id, trade_date)
        assert any(value is not None for value in values), template_id

    def test_ohlc_dependent_templates_registered(self) -> None:
        """候选池使用的高低价依赖集合只包含已注册模板，且含低振幅动量。"""
        ids = set(build_default_registry().ids())
        assert OHLC_DEPENDENT_TEMPLATE_IDS <= ids
        assert "low_amplitude_momentum" in OHLC_DEPENDENT_TEMPLATE_IDS


# ── 横截面标准化 ─────────────────────────────────────────────────────────


class TestCrossSectionNormalization:
    """平均秩与逐因子标准化。"""

    def test_rank_ties_share_average_rank(self) -> None:
        """并列值得分相同。"""
        assert normalize_rank({"a": 1.0, "b": 1.0, "c": 2.0}) == {
            "a": 50.0,
            "b": 50.0,
            "c": 100.0,
        }

    def test_zscore_keeps_missing_as_none(self) -> None:
        """有效样本不足时缺失键保持 None。"""
        assert normalize_zscore({"a": 1.0, "b": None}) == {"a": 0.0, "b": None}

    @staticmethod
    def _context(values: dict[tuple[str, str], float]) -> EngineContext:
        codes = sorted({code for code, _ in values})
        return EngineContext(
            trade_date=date(2025, 1, 2),
            universe=[{"index_code": code, "name_cn": code} for code in codes],
            asset_factors=values,
        )

    @pytest.mark.parametrize("mode", ["rank", "zscore"])
    def test_factor_scale_does_not_dominate(self, mode: str) -> None:
        """把一个因子整体放大 1000 倍不改变横截面得分（量纲不再影响权重）。"""
        a = {"x": 1.0, "y": 2.0, "z": 3.0}
        b = {"x": 3.0, "y": 1.0, "z": 2.0}
        config = ScoreConfig(factors={"a": 1.0, "b": 1.0}, scoring_mode=mode)
        base = CrossSectionScorer().calculate(
            config,
            self._context(
                {**{(c, "a"): v for c, v in a.items()}, **{(c, "b"): v for c, v in b.items()}}
            ),
        )
        scaled = CrossSectionScorer().calculate(
            config,
            self._context(
                {
                    **{(c, "a"): v for c, v in a.items()},
                    **{(c, "b"): v * 1000 for c, v in b.items()},
                }
            ),
        )
        assert base == scaled

    def test_negative_weight_reverses_direction(self) -> None:
        """负权重表示该因子越低越好。"""
        config = ScoreConfig(factors={"a": -1.0}, scoring_mode="rank")
        scores = CrossSectionScorer().calculate(
            config, self._context({("x", "a"): 1.0, ("y", "a"): 5.0})
        )
        assert scores["x"] > scores["y"]


# ── 现算编排 ─────────────────────────────────────────────────────────────


class TestComputeOrchestration:
    """市场级广播与面板轴。"""

    def test_market_template_computed_once_and_broadcast(self) -> None:
        """市场级模板只计算一次，结果广播到全部指数。"""
        registry = build_default_registry()
        service = FactorComputeService(MagicMock(), registry)
        instance = registry.resolve_params("breadth_ma_pct")
        ctx = _full_ctx()
        calls: list[str] = []
        original = instance.computer.compute_batch

        def _spy(code: str, dates: list[date], context: FactorContext) -> Any:
            calls.append(code)
            return original(code, dates, context)

        with patch.object(type(instance.computer), "compute_batch", autospec=True) as mocked:
            mocked.side_effect = lambda _self, code, dates, context: _spy(code, dates, context)
            matrix = service.compute_matrix(
                [instance], ["000300", "000905"], TARGET_DATES, ctx=ctx, include_panels=False
            )
        assert calls == ["000300"]
        day = matrix.day(TARGET_DATES[0])
        assert day[("000300", "breadth_ma_pct")] == day[("000905", "breadth_ma_pct")]
        assert day[("000300", "breadth_ma_pct")] is not None

    def test_panel_axis_covers_requested_range(self) -> None:
        """多日请求的面板轴覆盖整个区间并向前预留 332 个交易日预热。"""
        all_days = [date(2020, 1, 1) + timedelta(days=k) for k in range(2000)]
        service = FactorComputeService(MagicMock(), build_default_registry())

        class _Repo:
            def __init__(self, _db: Any) -> None:
                pass

            def find_all_trading_dates(self, start: date, end: date) -> list[date]:
                return [day for day in all_days if start <= day <= end]

        with patch("quant_etf_api.factors.compute.IndexDailyBarRepository", _Repo):
            start, end = date(2023, 1, 1), date(2024, 6, 30)
            axis = service._panel_calculation_dates(start, end)
        assert axis[-1] == end
        first_target = axis.index(start)
        assert first_target == 332


# ── 行业选择缓存 ─────────────────────────────────────────────────────────


class TestIndustrySelectionCache:
    """行业选择缓存的失效与空结果策略。"""

    def setup_method(self) -> None:
        panel_module._INDUSTRY_SELECTION_CACHE.clear()
        panel_module._INDUSTRY_SELECTION_CACHE_STAMP[0] = None

    def teardown_method(self) -> None:
        panel_module._INDUSTRY_SELECTION_CACHE.clear()
        panel_module._INDUSTRY_SELECTION_CACHE_STAMP[0] = None

    def test_unsynced_date_is_not_cached_as_missing(self) -> None:
        """晚于最新行业日线的日期空结果不缓存，数据同步后可重新计算。"""
        trade_date = date(2099, 1, 2)
        service = IndexFactorPanelService(MagicMock())
        with (
            patch.object(service, "_industry_data_stamp", return_value=date(2099, 1, 1)),
            patch.object(
                service, "_compute_industry_selection_from_source", return_value={}
            ) as compute,
        ):
            assert service._build_industry_selection_from_source([trade_date]) == {}
            assert service._build_industry_selection_from_source([trade_date]) == {}
        assert compute.call_count == 2
        assert trade_date not in panel_module._INDUSTRY_SELECTION_CACHE

    def test_stamp_change_invalidates_cache(self) -> None:
        """行业日线版本变化后重新计算。"""
        trade_date = date(2099, 1, 2)
        service = IndexFactorPanelService(MagicMock())
        selection = {trade_date: {"801010": 1.0}}
        with patch.object(
            service, "_compute_industry_selection_from_source", return_value=selection
        ) as compute:
            with patch.object(service, "_industry_data_stamp", return_value=trade_date):
                service._build_industry_selection_from_source([trade_date])
                service._build_industry_selection_from_source([trade_date])
            with patch.object(
                service, "_industry_data_stamp", return_value=trade_date + timedelta(days=1)
            ):
                service._build_industry_selection_from_source([trade_date])
        assert compute.call_count == 2

    def test_missing_industry_bars_yield_empty_selection(self) -> None:
        """行业日线缺失时返回空选择，而不是让整次计算失败。"""
        with patch(
            "quant_etf_api.services.industry_factor_service.IndustryFactorService.build_panels",
            side_effect=ValueError("行业日线无数据"),
        ):
            result = IndexFactorPanelService(MagicMock())._compute_industry_selection_from_source(
                [date(2099, 1, 2)]
            )
        assert result == {}
