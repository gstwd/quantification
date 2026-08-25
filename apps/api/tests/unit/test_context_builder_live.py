"""ContextBuilder 实时模式单元测试。

验证实时查询路径的只读约束（C3）：
- ContextBuilder.build 不产生任何写操作（不计算因子、不入队、不 commit）
- detect_missing_factors 只读返回三态缺失原因（C2）
- 补算入队由服务层 StrategyDecisionService.ensure_live_factors 触发
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from quant_etf_api.engine.config import ScoreConfig, StrategyConfig
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.factors.base import MissingReason
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    IndexDailyBarModel,
    IndexValuationModel,
)


def _make_config() -> StrategyConfig:
    """构建仅依赖 momentum 因子的测试策略。"""
    return StrategyConfig(
        strategy_id="test",
        display_name="测试策略",
        score=ScoreConfig(factors={"momentum": 1.0}),
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
    db.query(IndexDailyBarModel)
    db.query(IndexValuationModel)

    # 有效交易日回退查询：无数据则原样返回 trade_date
    chains[
        IndexDailyBarModel.trade_date
    ].filter.return_value.order_by.return_value.limit.return_value.first.return_value = None
    # 活跃指数列表
    chains[BenchmarkIndexModel].filter.return_value.order_by.return_value.all.return_value = [
        fake_index
    ]
    # 日线与估值查询均为空（index_codes 过滤后二次 filter）
    chains[IndexDailyBarModel].filter.return_value.filter.return_value.all.return_value = []
    chains[IndexValuationModel].filter.return_value.filter.return_value.all.return_value = []

    provider = provider or MagicMock(spec=FactorProvider)
    provider.load_asset_factors.return_value = {}
    provider.load_market_factors.return_value = {}
    provider.collect_required_factor_ids.side_effect = lambda cfg: list(cfg.score.factors.keys())
    return ContextBuilder(db, factor_provider=provider, registry=MagicMock())


class TestContextBuilderLive:
    """实时上下文构建只读约束测试。"""

    def test_build_live_is_read_only(self) -> None:
        """build 不应入队补算任务，也不应产生任何写操作。"""
        db, chains = _make_mock_db()
        builder = _make_live_context(db, chains)

        ctx = builder.build(_make_config(), date(2025, 1, 15))

        assert ctx.trade_date == date(2025, 1, 15)
        assert [u["index_code"] for u in ctx.universe] == ["000300"]
        # 只读约束：不调用 commit（写路径移出实时查询链路）
        db.commit.assert_not_called()
        # 补算入队职责已上移到服务层，ContextBuilder 不直接入队
        assert not hasattr(builder, "_enqueue")

    def test_detect_missing_factors_three_states(self) -> None:
        """缺失检测应区分因子未注册/未计算/数据不足三种语义。"""
        db, chains = _make_mock_db()
        builder = _make_live_context(db, chains)

        builder._registry.specs.return_value = [SimpleNamespace(factor_id="momentum")]

        # 因子未计算：任何资产都没有该因子行 → NOT_COMPUTED
        missing = builder.detect_missing_factors(_make_config(), ["000300"], {})
        assert missing["momentum"] == MissingReason.NOT_COMPUTED.value

        # 数据不足：因子行存在但数值为 NULL → INSUFFICIENT_DATA
        missing = builder.detect_missing_factors(
            _make_config(), ["000300"], {("000300", "momentum"): None}
        )
        assert missing["momentum"] == MissingReason.INSUFFICIENT_DATA.value

        # 因子未知：未注册 → FACTOR_UNKNOWN
        cfg_unknown = _make_config()
        cfg_unknown.score.factors = {"typo_factor": 1.0}
        missing = builder.detect_missing_factors(cfg_unknown, ["000300"], {})
        assert missing["typo_factor"] == MissingReason.FACTOR_UNKNOWN.value

    def test_detect_missing_factors_skips_ok_factors(self) -> None:
        """全部因子有值时缺失字典应为空。"""
        db, chains = _make_mock_db()
        builder = _make_live_context(db, chains)
        builder._registry.specs.return_value = [SimpleNamespace(factor_id="momentum")]

        missing = builder.detect_missing_factors(
            _make_config(), ["000300"], {("000300", "momentum"): 50.0}
        )
        assert missing == {}
