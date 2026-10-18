"""回测回望窗口（B2）单元测试。

覆盖：
- 回望自然日数从因子模板注册表推导，与实时模式口径一致
- BacktestService 按注册表推导回望窗口，行情与估值加载共用同一窗口
- 滚动估值分位等长窗口估值模板不抬高全局窗口，按策略单独扩展估值预加载
- 预热期估算：前 N 个交易日因子数据不足的天数提示
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from quant_etf_api.engine.config import FactorAliasConfig, ScoreConfig, StrategyConfig
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.factors.base import FactorSpec
from quant_etf_api.factors.catalog import (
    STRATEGY_SCOPED_VALUATION_TEMPLATE_IDS,
    FactorTemplateRegistry,
    build_default_registry,
    build_template,
)
from quant_etf_api.factors.templates import FactorTemplate
from quant_etf_api.services.backtest_diagnostics import estimate_warmup_trading_days
from quant_etf_api.services.backtest_service import BacktestService


class _StubComputer:
    """仅用于构造模板桩的因子计算器，只暴露 spec。"""

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


def _stub_template(template_id: str, lookback_days: int) -> FactorTemplate:
    """构造仅用于回望推导测试的模板桩。

    Args:
        template_id: 模板标识。
        lookback_days: 模板声明的回望自然日数。

    Returns:
        FactorTemplate。
    """
    return build_template(
        template_id, lambda _params: _StubComputer(template_id, lookback_days)
    )


class TestMaxLookbackDays:
    """回望自然日数推导测试。"""

    def test_default_registry_returns_max_lookback(self) -> None:
        """全局窗口 = 默认注册表（不含按策略扩展估值的模板）最大 lookback（估值百分位/ERP 为 730）。"""
        registry = build_default_registry()
        assert (
            max(
                template.lookback_days()
                for template in registry.all()
                if template.template_id not in STRATEGY_SCOPED_VALUATION_TEMPLATE_IDS
            )
            == 730
        )

    def test_strategy_scoped_templates_only_need_valuation(self) -> None:
        """豁免全局窗口的模板只能依赖估值数据（行情预加载无需为它们拉长）。"""
        registry = build_default_registry()
        for template_id in STRATEGY_SCOPED_VALUATION_TEMPLATE_IDS:
            template = registry.get(template_id)
            assert template is not None
            assert template.required_data == ("index_valuation",)

    def test_empty_instances_return_default(self) -> None:
        """无实例时应返回默认 90 天。"""
        assert FactorTemplateRegistry.max_lookback_days([]) == 90

    def test_custom_templates_return_max(self) -> None:
        """多模板时应返回声明的最大 lookback。"""
        registry = FactorTemplateRegistry()
        registry.register(_stub_template("long_momentum", 175))
        registry.register(_stub_template("ma_short", 15))
        instances = [registry.resolve(template.template_id) for template in registry.all()]
        assert FactorTemplateRegistry.max_lookback_days(instances) == 175


class TestBacktestServiceLookback:
    """BacktestService 回望窗口相关行为测试。"""

    def test_get_lookback_days_uses_registry(self) -> None:
        """回望天数应由注册表模板的最大 lookback 推导（默认注册表为 730）。"""
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


def _valuation_config(aliases: dict[str, FactorAliasConfig], refs: list[str]) -> StrategyConfig:
    """评分引用给定因子的最小策略配置。"""
    return StrategyConfig(
        strategy_id="v",
        display_name="v",
        factor_aliases=aliases,
        score=ScoreConfig(factors={ref: 1.0 for ref in refs}),
    )


class TestEnsureValuationHistory:
    """长窗口估值模板按策略扩展估值预加载。"""

    _DATES = [date(2018, 1, 2), date(2018, 1, 3)]

    def _svc(self) -> BacktestService:
        """使用真实注册表与因子解析的回测服务。"""
        svc = BacktestService(db=MagicMock())
        svc._factor_provider = FactorProvider(registry=build_default_registry())
        return svc

    def test_default_strategy_not_extended(self) -> None:
        """未引用长窗口估值模板时不补加载、原样返回同一字典。"""
        svc = self._svc()
        cached = {("000852", date(2017, 1, 3)): object()}
        with patch(
            "quant_etf_api.services.backtest_service.IndexValuationRepository"
        ) as mock_repo_cls:
            result, extended = svc._ensure_valuation_history(
                _valuation_config({}, ["pe_percentile", "rsi"]), self._DATES, ["000852"], cached
            )
        assert result is cached and extended is None
        mock_repo_cls.assert_not_called()

    def test_short_window_within_global_not_extended(self) -> None:
        """实例回望不超过全局窗口（730 天）时不补加载。"""
        aliases = {
            "v": FactorAliasConfig(template_id="valuation_percentile", params={"window": 400})
        }
        svc = self._svc()
        with patch("quant_etf_api.services.backtest_service.IndexValuationRepository"):
            _result, extended = svc._ensure_valuation_history(
                _valuation_config(aliases, ["v"]), self._DATES, ["000852"], {}
            )
        assert extended is None

    def test_long_window_extends_without_mutating_cache(self) -> None:
        """5 年窗口补加载全局窗口之前的估值行，合并进新字典而不改写共享缓存。"""
        aliases = {"v": FactorAliasConfig(template_id="valuation_percentile", params={})}
        svc = self._svc()
        cached_key = ("000852", date(2017, 1, 3))
        cached = {cached_key: "global"}
        old_key = ("000852", date(2013, 1, 4))
        with patch(
            "quant_etf_api.services.backtest_service.IndexValuationRepository"
        ) as mock_repo_cls:
            mock_repo_cls.return_value.find_range.return_value = {old_key: "old"}
            result, extended = svc._ensure_valuation_history(
                _valuation_config(aliases, ["v"]), self._DATES, ["000852", "000300"], cached
            )
        assert extended == 2010
        start, end, codes = mock_repo_cls.return_value.find_range.call_args.args
        assert start == self._DATES[0] - timedelta(days=2010)
        assert end == self._DATES[0] - timedelta(days=731)
        assert codes == ["000852", "000300"]
        assert result == {cached_key: "global", old_key: "old"}
        assert cached == {cached_key: "global"}
        # 全局窗口不因长窗口模板而变化
        assert svc._get_lookback_days() == 730


class TestEstimateWarmupTradingDays:
    """预热期估算测试。"""

    def _make_svc(self) -> BacktestService:
        """构建测试用 BacktestService 实例。"""
        return BacktestService(db=MagicMock())

    def test_returns_max_first_full_date(self) -> None:
        """应取各因子首次全覆盖日期的最大值作为预热期。"""
        dates = [date(2024, 6, 1), date(2024, 6, 2), date(2024, 6, 3), date(2024, 6, 4)]
        precomputed = {
            dates[0]: {("000300", "return_120d"): None, ("000300", "ma_5d"): 1.0},
            dates[1]: {("000300", "return_120d"): None, ("000300", "ma_5d"): 1.1},
            dates[2]: {("000300", "return_120d"): None, ("000300", "ma_5d"): 1.2},
            dates[3]: {("000300", "return_120d"): 5.0, ("000300", "ma_5d"): 1.3},
        }
        warmup = estimate_warmup_trading_days(
            precomputed, dates, ["000300"], ["return_120d", "ma_5d"]
        )
        # return_120d 第 4 个交易日才有效（index=3），ma_5d 首日有效（index=0）
        assert warmup == 3

    def test_ignores_never_full_factor(self) -> None:
        """从未达到全指数覆盖的因子不应参与预热统计。"""
        dates = [date(2024, 6, 1), date(2024, 6, 2)]
        precomputed = {
            dates[0]: {("000300", "pe_percentile"): 50.0, ("399001", "pe_percentile"): None},
            dates[1]: {("000300", "pe_percentile"): 51.0, ("399001", "pe_percentile"): None},
        }
        warmup = estimate_warmup_trading_days(
            precomputed, dates, ["000300", "399001"], ["pe_percentile"]
        )
        assert warmup == 0

    def test_empty_inputs_return_zero(self) -> None:
        """空因子列表或空交易日列表应返回 0。"""
        assert estimate_warmup_trading_days({}, [], ["000300"], []) == 0
