"""回测回望窗口（B2）单元测试。

覆盖：
- max_lookback_days 从注册表推导最大回望自然日数，与实时模式口径一致
- BacktestService 按注册表推导回望窗口，行情与估值加载共用同一窗口
- 预热期估算：前 N 个交易日因子数据不足的天数提示
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from quant_etf_api.factors.base import FactorSpec
from quant_etf_api.factors.registry import (
    FactorRegistry,
    build_default_factor_registry,
    max_lookback_days,
)
from quant_etf_api.services.backtest_service import BacktestService


class _StubComputer:
    """仅用于构造注册表的因子计算器桩，只暴露 spec。"""

    def __init__(self, factor_id: str, lookback_days: int) -> None:
        """初始化桩计算器。

        Args:
            factor_id: 因子标识。
            lookback_days: 声明的回望自然日数。
        """
        self._factor_id = factor_id
        self._lookback_days = lookback_days

    @property
    def spec(self) -> FactorSpec:
        """返回因子元数据。"""
        return FactorSpec(
            factor_id=self._factor_id,
            name="stub",
            category="momentum",
            version="1.0.0",
            description="stub",
            lookback_days=self._lookback_days,
        )


class TestMaxLookbackDays:
    """max_lookback_days 推导测试。"""

    def test_default_registry_returns_max_lookback(self) -> None:
        """默认注册表应返回全部因子的最大 lookback（估值百分位/ERP 为 730）。"""
        registry = build_default_factor_registry()
        assert max_lookback_days(registry) == 730

    def test_empty_registry_returns_default(self) -> None:
        """空注册表应返回默认 90 天。"""
        assert max_lookback_days(FactorRegistry()) == 90

    def test_custom_registry_returns_max(self) -> None:
        """自定义注册表应返回声明的最大 lookback。"""
        registry = FactorRegistry()
        registry.register(_StubComputer("return_120d", 175))
        registry.register(_StubComputer("ma_5d", 15))
        assert max_lookback_days(registry) == 175


class TestBacktestServiceLookback:
    """BacktestService 回望窗口相关行为测试。"""

    def test_get_lookback_days_uses_registry(self) -> None:
        """回望天数应由注册表最大 lookback 推导（默认注册表为 730）。"""
        svc = BacktestService(db=MagicMock())
        assert svc._get_lookback_days() == 730

    def test_load_all_index_bars_uses_lookback_window(self) -> None:
        """行情加载应使用回测起点前推回望窗口的日期范围。"""
        svc = BacktestService(db=MagicMock())
        svc._index_bar_repo = MagicMock()
        trading_dates = [date(2024, 6, 1), date(2024, 6, 2)]

        svc._load_all_index_bars(trading_dates, ["000300"])

        svc._index_bar_repo.find_all_date_range.assert_called_once()
        call_args = svc._index_bar_repo.find_all_date_range.call_args
        start, end = call_args.args
        index_codes = call_args.kwargs.get("index_codes")
        assert end == trading_dates[-1]
        assert index_codes == ["000300"]
        # 回望窗口 = 起点 - 730 自然日
        assert start == date(2024, 6, 1) - timedelta(days=730)

    def test_load_all_valuation_uses_lookback_window(self) -> None:
        """估值加载应使用与行情相同的回望窗口（供历史分布因子使用）。"""
        svc = BacktestService(db=MagicMock())
        trading_dates = [date(2024, 6, 1), date(2024, 6, 2)]
        with patch(
            "quant_etf_api.services.backtest_service.IndexValuationRepository"
        ) as mock_repo_cls:
            mock_repo = mock_repo_cls.return_value
            svc._load_all_valuation(trading_dates, ["000300"])
            mock_repo.find_range.assert_called_once()
            start, end, index_codes = mock_repo.find_range.call_args.args
            assert start == date(2024, 6, 1) - timedelta(days=730)
            assert end == trading_dates[-1]
            assert index_codes == ["000300"]

    def test_load_all_valuation_empty_dates_returns_empty(self) -> None:
        """无交易日时估值加载应返回空字典。"""
        svc = BacktestService(db=MagicMock())
        assert svc._load_all_valuation([], ["000300"]) == {}


class TestEstimateWarmupTradingDays:
    """预热期估算测试。"""

    def _make_svc(self) -> BacktestService:
        """构建测试用 BacktestService 实例。"""
        return BacktestService(db=MagicMock())

    def test_returns_max_first_full_date(self) -> None:
        """应取各因子首次全覆盖日期的最大值作为预热期。"""
        svc = self._make_svc()
        dates = [date(2024, 6, 1), date(2024, 6, 2), date(2024, 6, 3), date(2024, 6, 4)]
        precomputed = {
            dates[0]: {("000300", "return_120d"): None, ("000300", "ma_5d"): 1.0},
            dates[1]: {("000300", "return_120d"): None, ("000300", "ma_5d"): 1.1},
            dates[2]: {("000300", "return_120d"): None, ("000300", "ma_5d"): 1.2},
            dates[3]: {("000300", "return_120d"): 5.0, ("000300", "ma_5d"): 1.3},
        }
        warmup = svc._estimate_warmup_trading_days(
            precomputed, dates, ["000300"], ["return_120d", "ma_5d"]
        )
        # return_120d 第 4 个交易日才有效（index=3），ma_5d 首日有效（index=0）
        assert warmup == 3

    def test_ignores_never_full_factor(self) -> None:
        """从未达到全指数覆盖的因子不应参与预热统计。"""
        svc = self._make_svc()
        dates = [date(2024, 6, 1), date(2024, 6, 2)]
        precomputed = {
            dates[0]: {("000300", "pe_percentile"): 50.0, ("399001", "pe_percentile"): None},
            dates[1]: {("000300", "pe_percentile"): 51.0, ("399001", "pe_percentile"): None},
        }
        warmup = svc._estimate_warmup_trading_days(
            precomputed, dates, ["000300", "399001"], ["pe_percentile"]
        )
        assert warmup == 0

    def test_empty_inputs_return_zero(self) -> None:
        """空因子列表或空交易日列表应返回 0。"""
        svc = self._make_svc()
        assert svc._estimate_warmup_trading_days({}, [], ["000300"], []) == 0
