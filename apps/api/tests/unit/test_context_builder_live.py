"""ContextBuilder 实时模式单元测试。

验证实时查询路径不再在请求线程内重算因子（写路径移除），
因子缺失时改为入队 factor_computation 后台任务。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from quant_etf_api.engine.config import ScoreConfig, StrategyConfig
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.infra.db.models.core import BenchmarkIndexModel, IndexDailyBarModel, IndexValuationModel


def _make_config() -> StrategyConfig:
    """构建仅依赖 momentum 因子的测试策略。"""
    return StrategyConfig(
        strategy_id="test",
        display_name="测试策略",
        score=ScoreConfig(factors={"momentum": 1.0}),
    )


def _make_mock_db() -> MagicMock:
    """构建按模型区分的 mock Session。"""
    db = MagicMock()
    chains: dict = {}

    def query_side_effect(model):
        if model not in chains:
            chains[model] = MagicMock()
        return chains[model]

    db.query.side_effect = query_side_effect
    return db, chains


class TestContextBuilderLive:
    """实时上下文构建去写路径测试。"""

    def test_missing_factors_enqueues_not_computes(self, monkeypatch) -> None:
        """因子缺失时应入队 factor_computation，且不调用 FactorService/commit。"""
        db, chains = _make_mock_db()
        fake_index = SimpleNamespace(index_code="000300", name_cn="沪深300", category="broad_index")

        # 先触发各查询链创建，再配置返回值
        db.query(IndexDailyBarModel.trade_date)
        db.query(BenchmarkIndexModel)
        db.query(IndexDailyBarModel)
        db.query(IndexValuationModel)
        # 有效交易日回退查询：无数据则原样返回 trade_date
        chains[IndexDailyBarModel.trade_date].filter.return_value.order_by.return_value.limit.return_value.first.return_value = None
        # 活跃指数列表
        chains[BenchmarkIndexModel].filter.return_value.all.return_value = [fake_index]
        # 日线与估值查询均为空
        chains[IndexDailyBarModel].filter.return_value.all.return_value = []
        chains[IndexValuationModel].filter.return_value.all.return_value = []

        provider = MagicMock(spec=FactorProvider)
        provider.load_asset_factors.return_value = {}
        provider.load_market_factors.return_value = {}
        provider.collect_required_factor_ids.return_value = ["momentum"]

        fake_queue = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue
        )

        builder = ContextBuilder(db, factor_provider=provider, registry=MagicMock())
        ctx = builder.build(_make_config(), date(2025, 1, 15))

        assert ctx.trade_date == date(2025, 1, 15)
        assert fake_queue.enqueue.call_count == 1
        job_type, payload = fake_queue.enqueue.call_args.args
        assert job_type == "factor_computation"
        assert payload == {"trade_date": "2025-01-15"}
        assert fake_queue.enqueue.call_args.kwargs["job_key"] == "factor_computation:2025-01-15"
        # 写路径移除：不应调用 commit
        db.commit.assert_not_called()
