"""执行模型口径在优化/稳健性/研究批量三条链路上的透传单元测试。

执行模型以前只是回测请求体上的参数：优化会话与稳健性批次派生子回测时不带它，
只能跑在默认的 T+1 开盘口径上，"T+1 收盘口径的优化"无从表达。本测试锁定
三条链路的透传契约，避免"命令行传了、服务层丢了"的静默退化。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from quant_etf_api.infra.db.models.core import RobustnessRunModel
from quant_etf_api.services.research_batch_service import ResearchBatchService
from quant_etf_api.services.robustness_service import RobustnessService


class TestRobustnessExecutionModel:
    """稳健性批次：执行模型写入批次行，并作用于全部变体回测。"""

    def _service(self) -> RobustnessService:
        """构造不连库的 RobustnessService 替身。"""
        svc = RobustnessService(db=MagicMock())
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.return_value = SimpleNamespace(
            version="1.0.0", config_json={"score": {"factors": {"return_20d": 1.0}}}
        )
        svc._index_bar_repo = MagicMock()
        svc._index_bar_repo.find_all_trading_dates.return_value = [
            date(2024, 1, i + 1) for i in range(8)
        ]
        # 只造一个变体，减少无关构造
        svc._build_variants = MagicMock(
            return_value=[{"label": "k1", "kind": "knob", "knob": "rank.top_n", "value": 3}]
        )
        svc._create_variant_strategy = MagicMock(return_value="base__rb1_k1")
        svc._create_variant_backtest = MagicMock(return_value="bt_rb")
        return svc

    def test_batch_row_and_variant_backtests_share_model(self) -> None:
        """批次行记录执行模型，变体回测按同一模型创建。"""
        svc = self._service()

        result = svc.create(
            strategy_id="base",
            kind="scan",
            windows=2,
            async_mode=False,
            execution_model="t_plus_1_close",
        )

        model = svc._db.add.call_args.args[0]
        assert isinstance(model, RobustnessRunModel)
        assert model.execution_model == "t_plus_1_close"
        assert model.scan_params["execution_model"] == "t_plus_1_close"
        used = {call.args[5] for call in svc._create_variant_backtest.call_args_list}
        assert used == {"t_plus_1_close"}
        assert result["execution_model"] == "t_plus_1_close"

    def test_summary_exposes_execution_model(self) -> None:
        """批次摘要带出执行模型（口径指纹，避免跨模型比较指标）。"""
        row = RobustnessRunModel(
            robustness_id="rb-1",
            strategy_id="base",
            strategy_version="1.0.0",
            baseline_config_hash="h",
            execution_model="t_plus_1_close",
            kind="scan",
            status="running",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 8),
            trial_count=4,
        )
        summary = RobustnessService._to_summary(row)
        assert summary.execution_model == "t_plus_1_close"


class TestResearchBatchExecutionModel:
    """研究批量评估：探索口径与验收口径必须可对齐。"""

    def _service(self) -> ResearchBatchService:
        """构造不连库的 ResearchBatchService 替身。"""
        svc = ResearchBatchService(db=MagicMock())
        svc._config_svc = MagicMock()
        svc._config_svc.get_config.return_value = SimpleNamespace(
            display_name="基线",
            version="1.0.0",
            description="",
            frequency="weekly",
            config_json={"score": {"factors": {"return_20d": 1.0}}},
        )
        svc._backtest_svc = MagicMock()
        return svc

    def test_build_row_uses_requested_model(self) -> None:
        """临时回测行的执行模型来自调用参数，而非硬编码开盘口径。"""
        svc = self._service()
        row = svc._build_row(
            strategy_id="base",
            config=MagicMock(index_codes=[]),
            window={"label": "W0", "start": "2024-01-01", "end": "2024-01-08"},
            cost_bps=10.0,
            execution_model="t_plus_1_close",
        )
        assert row.params["_execution_model"] == "t_plus_1_close"
