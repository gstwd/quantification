"""P4 配置驱动引擎校验缺口修复测试。

覆盖：
- 因子引用收集（含排名子因子、compare_to、regime 嵌套）
- validate_config / validate_parsed 对未知因子、停用因子、未知变换函数的快速失败
- 运行期兜底：run_allocation / create_backtest 校验失败即抛错，星标摘要跳过坏策略
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy.orm import Session

from quant_etf_api.engine.config import (
    FactorAliasConfig,
    FilterConfig,
    FilterRule,
    PortfolioConfig,
    RankConfig,
    RegimeRuleConfig,
    ScoreConfig,
    StrategyConfig,
    TimingConfig,
)
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.factors.catalog import FactorResolutionError, build_default_registry
from quant_etf_api.infra.db.repositories.strategy_config import StrategyConfigRepository
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.schemas.strategy import StrategyValidationResult
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import StrategyConfigService
from quant_etf_api.services.strategy_decision_service import StrategyDecisionService
from quant_etf_api.services.strategy_service import StrategyService


def _registry_ids() -> set[str]:
    """返回默认模板注册表中全部模板 ID。"""
    return set(build_default_registry().ids())


class _WeekdayCalendar:
    """按工作日判断交易日的替身日历（用例不需要真实节假日日历）。

    测试不应依赖网络：星标摘要会构建调仓调度器，而严格口径（C1）下
    日历必须来自真实数据源或本地表，这里注入只按工作日判断的替身。
    """

    def is_trading_day(self, day: date) -> bool:
        """工作日视为交易日。"""
        return day.weekday() < 5

    def latest_trading_day(self, day: date) -> date:
        """回退到最近的工作日。"""
        while day.weekday() >= 5:
            day = day - timedelta(days=1)
        return day

    def next_trading_day(self, day: date) -> date:
        """前进到下一个工作日。"""
        day = day + timedelta(days=1)
        while day.weekday() >= 5:
            day = day + timedelta(days=1)
        return day

    def trading_days_between(self, start: date, end: date) -> list[date]:
        """区间内的工作日列表。"""
        days: list[date] = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                days.append(current)
            current = current + timedelta(days=1)
        return days


class _FakeRow:
    """极简 ORM 行替身，仅暴露 factor_id。"""

    def __init__(self, factor_id: str) -> None:
        self.factor_id = factor_id


def _make_service(active_ids: set[str] | None = None) -> StrategyConfigService:
    """构造策略配置服务，使因子查询返回指定因子集。

    使用 MagicMock 会话驱动真实的 FactorDefinitionRepository.find_active()
    查询链，确保测试覆盖真实仓库代码路径（而非替身方法）。

    Args:
        active_ids: 应视为已启用的因子 ID 集合，缺省为全部注册因子。

    Returns:
        已注入假因子查询结果的 StrategyConfigService。
    """
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        _FakeRow(fid) for fid in (active_ids if active_ids is not None else _registry_ids())
    ]
    return StrategyConfigService(db=db)


def _valid_config_json() -> dict:
    """构造一个引用全部合法模板与别名的配置。"""
    return {
        "schema_version": "2",
        "score": {
            "factors": {"return_20d": 1.0, "ma_20d": 0.5, "pe_percentile": 0.8},
            "transforms": {"pe_percentile": "invert_percentile"},
        },
        "filters": {"rules": [{"factor": "return_20d", "op": "gt", "value": 0.0}]},
        "rank": {"momentum_factor": "return_20d", "valuation_factor": "pe_percentile"},
        "timing": {"factors": {"return_60d": 1.0}, "proxy_index_codes": ["000300"]},
        "portfolio": {"method": "equal_weight"},
        "factor_aliases": {
            "return_20d": {"template_id": "return", "params": {"period": 20}},
            "ma_20d": {"template_id": "sma", "params": {"period": 20}},
            "return_60d": {"template_id": "return", "params": {"period": 60}},
        },
    }


class TestCollectRequiredFactorIds:
    """因子引用收集完整性测试。"""

    def test_includes_all_reference_points(self) -> None:
        """评分、择时、过滤、compare_to、排名子因子、regime 嵌套全部收集。"""
        config = StrategyConfig(
            strategy_id="s1",
            display_name="测试",
            score=ScoreConfig(factors={"return_5d": 1.0}),
            timing=TimingConfig(factors={"return_60d": 1.0}),
            filters=FilterConfig(
                rules=[
                    FilterRule(factor="return_20d", op="gt", compare_to="ma_20d"),
                    FilterRule(factor="pe_percentile", op="gt", value=30.0),
                ]
            ),
            rank=RankConfig(
                sort_by="valuation_rank",
                momentum_factor="return_120d",
                valuation_factor="pb_percentile",
            ),
            regime_rules={
                "offensive": RegimeRuleConfig(
                    score=ScoreConfig(factors={"volume_ratio": 1.0}),
                    filters=FilterConfig(
                        rules=[FilterRule(factor="return_17d", op="gt", value=0.0)]
                    ),
                )
            },
        )

        ids = FactorProvider.collect_required_factor_ids(config)

        assert "return_5d" in ids
        assert "return_60d" in ids
        assert "return_20d" in ids
        assert "ma_20d" in ids
        assert "pe_percentile" in ids
        # 当前按估值子排名排序，仅估值子因子参与策略决策。
        assert "return_120d" not in ids
        assert "pb_percentile" in ids
        # regime 嵌套
        assert "volume_ratio" in ids
        assert "return_17d" in ids

    def test_ignores_display_only_sub_rank_factors(self) -> None:
        """排名按综合得分时，不应把仅用于展示的子排名因子视为策略依赖。"""
        config = StrategyConfig(
            strategy_id="s1",
            display_name="测试",
            score=ScoreConfig(factors={"return_5d": 1.0}),
            rank=RankConfig(
                sort_by="score",
                momentum_factor="return_120d",
                valuation_factor="pe_percentile",
            ),
        )

        ids = FactorProvider.collect_required_factor_ids(config)

        assert "return_5d" in ids
        assert "return_120d" not in ids
        assert "pe_percentile" not in ids


class TestResolveInstances:
    """因子引用解析测试。"""

    def test_alias_wins_over_template_default(self) -> None:
        """声明了别名时按别名的模板与参数解析实例。"""
        provider = FactorProvider(registry=build_default_registry())
        config = StrategyConfig(
            strategy_id="test",
            display_name="测试策略",
            score=ScoreConfig(factors={"trend_fast": 1.0}),
            factor_aliases={
                "trend_fast": FactorAliasConfig(template_id="sma", params={"period": 10})
            },
        )

        instances = provider.resolve_instances(config)

        assert instances["trend_fast"].template_id == "sma"
        assert instances["trend_fast"].params["period"] == 10

    def test_bare_template_id_uses_default_params(self) -> None:
        """未声明别名时按模板默认参数解析。"""
        provider = FactorProvider(registry=build_default_registry())
        config = StrategyConfig(
            strategy_id="test",
            display_name="测试策略",
            score=ScoreConfig(factors={"close_price": 1.0, "sma": 0.5}),
        )

        instances = provider.resolve_instances(config)

        assert instances["close_price"].template_id == "close_price"
        assert instances["sma"].template_id == "sma"
        assert instances["sma"].params["period"] == 20

    def test_unresolvable_reference_raises(self) -> None:
        """既非别名也非模板 ID 的引用解析失败。"""
        provider = FactorProvider(registry=build_default_registry())
        config = StrategyConfig(
            strategy_id="test",
            display_name="测试策略",
            score=ScoreConfig(factors={"ghost": 1.0}),
        )

        with pytest.raises(FactorResolutionError):
            provider.resolve_instances(config)


class TestValidateConfig:
    """配置校验的因子 ID 与变换函数校验。"""

    def test_valid_config_passes(self) -> None:
        """引用合法因子且变换函数存在时校验通过。"""
        result = _make_service().validate_config(_valid_config_json())
        assert result.valid
        assert result.errors == []

    def test_unknown_factor_in_score_rejected(self) -> None:
        """评分因子拼写错误时快速失败。"""
        cfg = _valid_config_json()
        cfg["score"]["factors"] = {"return_20d": 1.0, "return_999d": 1.0}
        result = _make_service().validate_config(cfg)
        assert not result.valid
        assert any("未知因子" in e and "return_999d" in e for e in result.errors)

    def test_unknown_factor_in_timing_rejected(self) -> None:
        """择时因子拼写错误时快速失败。"""
        cfg = _valid_config_json()
        cfg["timing"]["factors"] = {"no_such_timing_factor": 1.0}
        result = _make_service().validate_config(cfg)
        assert not result.valid
        assert any("未知因子" in e and "no_such_timing_factor" in e for e in result.errors)

    def test_unknown_compare_to_rejected(self) -> None:
        """过滤规则 compare_to 引用未知因子时快速失败。"""
        cfg = _valid_config_json()
        cfg["filters"] = {"rules": [{"factor": "return_20d", "op": "gt", "compare_to": "ma_999d"}]}
        result = _make_service().validate_config(cfg)
        assert not result.valid
        assert any("未知因子" in e and "ma_999d" in e for e in result.errors)

    def test_unknown_rank_sub_factor_rejected(self) -> None:
        """排名模块 momentum_factor / valuation_factor 引用未知因子时快速失败。"""
        cfg = _valid_config_json()
        cfg["rank"] = {"momentum_factor": "return_20d", "valuation_factor": "pe_999d"}
        result = _make_service().validate_config(cfg)
        assert not result.valid
        assert any("未知因子" in e and "pe_999d" in e for e in result.errors)

    def test_unknown_factor_in_regime_rules_rejected(self) -> None:
        """regime 条件化配置中引用未知因子时快速失败。"""
        cfg = _valid_config_json()
        cfg["regime_rules"] = {
            "defensive": {"score": {"factors": {"return_5d": 1.0, "ghost_factor": 0.5}}}
        }
        result = _make_service().validate_config(cfg)
        assert not result.valid
        assert any("未知因子" in e and "ghost_factor" in e for e in result.errors)

    def test_deactivated_template_rejected(self) -> None:
        """模板已注册但 DB 中已停用/未同步时被拒绝。"""
        active_ids = _registry_ids() - {"return"}
        result = _make_service(active_ids=active_ids).validate_config(_valid_config_json())
        assert not result.valid
        assert any("return" in e and "已停用或未同步" in e for e in result.errors)

    def test_unknown_transform_rejected(self) -> None:
        """未知变换函数名被提前拦截，避免运行时 KeyError。"""
        cfg = _valid_config_json()
        cfg["score"]["transforms"] = {"return_20d": "no_such_transform"}
        result = _make_service().validate_config(cfg)
        assert not result.valid
        assert any("未知变换函数 'no_such_transform'" in e for e in result.errors)

    def test_structural_errors_still_work(self) -> None:
        """原有结构校验（非法操作符等）保持有效。"""
        cfg = _valid_config_json()
        cfg["filters"] = {"rules": [{"factor": "return_20d", "op": "whatever", "value": 0.0}]}
        result = _make_service().validate_config(cfg)
        assert not result.valid
        assert any("操作符" in e for e in result.errors)

    def test_invalid_missing_strategy_rejected(self) -> None:
        """过滤规则 missing_strategy 非法值时校验失败。"""
        cfg = _valid_config_json()
        cfg["filters"] = {
            "rules": [
                {"factor": "return_20d", "op": "gt", "value": 0.0, "missing_strategy": "skip"}
            ]
        }
        result = _make_service().validate_config(cfg)
        assert not result.valid
        assert any("missing_strategy" in e for e in result.errors)

    def test_valid_missing_strategy_values_accepted(self) -> None:
        """missing_strategy 三个合法值（pass/fail/exclude）均通过校验。"""
        for strategy in ("pass", "fail", "exclude"):
            cfg = _valid_config_json()
            cfg["filters"] = {
                "rules": [
                    {
                        "factor": "return_20d",
                        "op": "gt",
                        "value": 0.0,
                        "missing_strategy": strategy,
                    }
                ]
            }
            result = _make_service().validate_config(cfg)
            assert result.valid, f"missing_strategy={strategy} 应通过校验: {result.errors}"


class TestRuntimeGuards:
    """运行期兜底：快速失败与聚合接口降级。"""

    def _make_config(self) -> StrategyConfig:
        """构造含 portfolio 的合法解析配置。"""
        return StrategyConfig(
            strategy_id="s1",
            display_name="测试",
            score=ScoreConfig(factors={"return_20d": 1.0}),
            rank=RankConfig(),
            portfolio=PortfolioConfig(method="equal_weight"),
        )

    def test_run_allocation_fails_fast_on_invalid_config(self) -> None:
        """run_allocation 校验失败时抛 ValueError，不再静默产出错误结果。"""
        svc = StrategyService(db=object())  # type: ignore[arg-type]
        invalid = StrategyValidationResult(valid=False, errors=["未知因子 'x'"])
        with (
            mock.patch.object(
                StrategyConfigService, "get_parsed_config", return_value=self._make_config()
            ),
            mock.patch.object(StrategyConfigService, "validate_parsed", return_value=invalid),
        ):
            with pytest.raises(ValueError, match="配置校验失败"):
                svc.run_allocation("s1")

    def test_starred_summary_skips_invalid_strategy(self) -> None:
        """星标摘要遇到校验失败的策略时跳过并继续返回其他策略。"""
        svc = StrategyService(db=object())  # type: ignore[arg-type]
        row_a = SimpleNamespace(strategy_id="a", display_name="坏策略", frequency="daily")
        row_b = SimpleNamespace(strategy_id="b", display_name="好策略", frequency="daily")
        cfg = self._make_config()
        fake_allocation = SimpleNamespace(
            data_date=date(2025, 1, 15),
            timing={},
            rankings=[],
            plan={},
        )

        def _fake_run_allocation(strategy_id: str, trade_date=None):
            if strategy_id == "a":
                raise ValueError("配置校验失败")
            return fake_allocation

        with (
            mock.patch.object(
                StrategyConfigRepository, "find_starred", return_value=[row_a, row_b]
            ),
            mock.patch.object(StrategyConfigService, "get_parsed_config", return_value=cfg),
            mock.patch.object(
                StrategyDecisionService,
                "run_allocation",
                side_effect=_fake_run_allocation,
            ),
            # 星标摘要会构建调仓调度器，需要真实交易日历（C1 严格口径）；
            # 用例不该依赖网络，这里注入只按工作日判断的替身日历
            mock.patch(
                "quant_etf_api.services.strategy_decision_service.resolve_trading_calendar",
                return_value=(_WeekdayCalendar(), "test"),
            ),
        ):
            summary = svc.get_starred_summary(trade_date=date(2025, 1, 15))

        assert len(summary.items) == 1
        assert summary.items[0].strategy_id == "b"

    def test_create_backtest_rejects_invalid_config(self) -> None:
        """create_backtest 创建前校验失败时抛 ValueError，且不落库。"""
        db = mock.MagicMock(spec=Session)
        svc = BacktestService(db=db)
        req = BacktestCreateRequest(
            strategy_id="s1",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        invalid = StrategyValidationResult(valid=False, errors=["未知因子 'x'"])
        with (
            mock.patch.object(
                StrategyConfigService, "get_parsed_config", return_value=self._make_config()
            ),
            mock.patch.object(StrategyConfigService, "validate_parsed", return_value=invalid),
        ):
            with pytest.raises(ValueError, match="配置校验失败"):
                svc.create_backtest(req)
        db.add.assert_not_called()

    def test_create_backtest_rejects_missing_config(self) -> None:
        """策略配置解析失败/不存在时直接拒绝，不再静默创建回测。"""
        db = mock.MagicMock(spec=Session)
        svc = BacktestService(db=db)
        req = BacktestCreateRequest(
            strategy_id="missing",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        with mock.patch.object(StrategyConfigService, "get_parsed_config", return_value=None):
            with pytest.raises(ValueError, match="配置不存在或解析失败"):
                svc.create_backtest(req)
        db.add.assert_not_called()

    def test_create_backtest_accepts_valid_config(self) -> None:
        """合法配置仍可正常创建回测记录。"""
        db = mock.MagicMock(spec=Session)
        svc = BacktestService(db=db)
        req = BacktestCreateRequest(
            strategy_id="s1",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        valid = StrategyValidationResult(valid=True, errors=[])
        with (
            mock.patch.object(
                StrategyConfigService, "get_parsed_config", return_value=self._make_config()
            ),
            mock.patch.object(StrategyConfigService, "validate_parsed", return_value=valid),
        ):
            summary = svc.create_backtest(req)
        assert summary.backtest_id
        assert summary.strategy_id == "s1"
        assert db.add.called
