"""ContextBuilder 实时模式单元测试。

验证实时查询路径的只读约束：
- ContextBuilder.build 不产生任何写操作（不入队补算、不 commit）
- insufficient_factors 只读返回计算失败 / 数据不足两类缺失原因
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import ScoreConfig, StrategyConfig
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.factors.base import MissingReason
from quant_etf_api.factors.catalog import get_factor_template_registry
from quant_etf_api.factors.compute import FactorMatrix
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    IndexDailyBarModel,
)


def _make_config() -> StrategyConfig:
    """构建仅依赖收盘价模板的测试策略。"""
    return StrategyConfig(
        strategy_id="test",
        display_name="测试策略",
        score=ScoreConfig(factors={"close_price": 1.0}),
    )


def _make_mock_db() -> tuple[MagicMock, dict]:
    """构建按模型区分的 mock Session。"""
    db = MagicMock()
    chains: dict = {}

    def query_side_effect(model):
        if model not in chains:
            chains[model] = MagicMock()
        return chains[model]

    db.query.side_effect = query_side_effect
    return db, chains


def _make_live_context(
    db: MagicMock,
    chains: dict,
    provider: FactorProvider | None = None,
) -> ContextBuilder:
    """构建带默认 mock 数据的实时 ContextBuilder。"""
    fake_index = SimpleNamespace(index_code="000300", name_cn="沪深300", category="broad_index")

    # 先触发各查询链创建，再配置返回值
    db.query(IndexDailyBarModel.trade_date)
    db.query(BenchmarkIndexModel)

    # 有效交易日回退查询：无数据则原样返回 trade_date
    chains[
        IndexDailyBarModel.trade_date
    ].filter.return_value.order_by.return_value.limit.return_value.first.return_value = None
    # 活跃指数列表
    chains[BenchmarkIndexModel].filter.return_value.order_by.return_value.all.return_value = [
        fake_index
    ]

    provider = provider or MagicMock(spec=FactorProvider)
    provider.load_asset_factor_matrix.return_value = FactorMatrix(
        values={date(2025, 1, 15): {("000300", "close_price"): 100.0}}
    )
    provider.load_market_factors.return_value = {}
    return ContextBuilder(db, factor_provider=provider, registry=get_factor_template_registry())


class TestContextBuilderLive:
    """实时上下文构建只读约束测试。"""

    def test_default_factor_provider_receives_registry(self) -> None:
        """默认供应器应接收模板注册表，以解析策略中的因子引用。"""
        db = MagicMock()
        registry = MagicMock()
        with patch("quant_etf_api.engine.context_builder.FactorProvider") as provider_class:
            ContextBuilder(db, registry=registry)

        provider_class.assert_called_once_with(db=db, registry=registry)

    def test_build_live_is_read_only(self) -> None:
        """build 不应入队补算任务，也不应产生任何写操作。"""
        db, chains = _make_mock_db()
        builder = _make_live_context(db, chains)

        ctx = builder.build(_make_config(), date(2025, 1, 15))

        assert ctx.trade_date == date(2025, 1, 15)
        assert [u["index_code"] for u in ctx.universe] == ["000300"]
        # 只读约束：不调用 commit（写路径移出实时查询链路）
        db.commit.assert_not_called()
        assert not hasattr(builder, "_enqueue")

    def test_insufficient_factors_reports_data_shortage(self) -> None:
        """取不到数值的因子应报告数据不足。"""
        builder = ContextBuilder(MagicMock(), factor_provider=MagicMock(spec=FactorProvider))
        context = EngineContext(
            trade_date=date(2025, 1, 15),
            universe=[{"index_code": "000300"}],
            asset_factors={("000300", "close_price"): None},
        )

        missing = builder.insufficient_factors(context)

        assert missing == {"close_price": MissingReason.INSUFFICIENT_DATA.value}

    def test_insufficient_factors_reports_compute_failure(self) -> None:
        """计算抛异常的因子应报告计算失败，而不是数据不足。"""
        builder = ContextBuilder(MagicMock(), factor_provider=MagicMock(spec=FactorProvider))
        context = EngineContext(
            trade_date=date(2025, 1, 15),
            universe=[{"index_code": "000300"}],
            asset_factors={},
            factor_failures={"close_price"},
        )

        missing = builder.insufficient_factors(context)

        assert missing == {"close_price": MissingReason.COMPUTE_FAILED.value}

    def test_insufficient_factors_skips_ok_factors(self) -> None:
        """全部因子有值时缺失字典应为空。"""
        builder = ContextBuilder(MagicMock(), factor_provider=MagicMock(spec=FactorProvider))
        context = EngineContext(
            trade_date=date(2025, 1, 15),
            universe=[{"index_code": "000300"}],
            asset_factors={("000300", "close_price"): 100.0},
        )

        assert builder.insufficient_factors(context) == {}

    def test_unresolvable_reference_fails_fast(self) -> None:
        """引用未知模板时解析失败，不静默产出空结果。"""
        from quant_etf_api.factors.catalog import FactorResolutionError

        provider = FactorProvider(registry=get_factor_template_registry())
        config = StrategyConfig(
            strategy_id="test",
            display_name="测试策略",
            score=ScoreConfig(factors={"ghost_factor": 1.0}),
        )

        with pytest.raises(FactorResolutionError):
            provider.resolve_instances(config)
