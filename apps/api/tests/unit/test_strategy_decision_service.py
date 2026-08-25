"""统一策略执行服务测试（C1/C3 收敛点）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import mock

from quant_etf_api.engine.config import ScoreConfig, StrategyConfig
from quant_etf_api.factors.base import MissingReason
from quant_etf_api.schemas.strategy import StrategyValidationResult
from quant_etf_api.services.strategy_decision_service import StrategyDecisionService


def _make_config() -> StrategyConfig:
    """构建测试策略配置。"""
    return StrategyConfig(
        strategy_id="s1",
        display_name="测试策略",
        score=ScoreConfig(factors={"return_20d": 1.0}),
    )


def _make_context(missing: dict[str, str] | None = None) -> SimpleNamespace:
    """构建带 universe/asset_factors 的伪上下文。"""
    return SimpleNamespace(
        trade_date=date(2025, 1, 15),
        universe=[{"index_code": "000300", "etf_code": "000300", "name_cn": "沪深300"}],
        asset_factors={("000300", "return_20d"): 3.0},
        _missing=missing or {},
    )


class TestEnsureLiveFactors:
    """服务层补算触发测试。"""

    def test_enqueues_for_not_computed_and_insufficient(self, monkeypatch) -> None:
        """NOT_COMPUTED 与 INSUFFICIENT_DATA 应入队 factor_computation。"""
        db = mock.MagicMock()
        svc = StrategyDecisionService(db=db)
        fake_queue = mock.MagicMock()
        monkeypatch.setattr("quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue)

        context = _make_context()
        svc._context_builder.detect_missing_factors = mock.MagicMock(
            return_value={
                "return_20d": MissingReason.NOT_COMPUTED.value,
                "pe_percentile": MissingReason.INSUFFICIENT_DATA.value,
            }
        )

        triggered = svc.ensure_live_factors(_make_config(), context)

        assert triggered == ["return_20d", "pe_percentile"]
        assert fake_queue.enqueue.call_count == 1
        job_type, payload = fake_queue.enqueue.call_args.args
        assert job_type == "factor_computation"
        assert payload == {"trade_date": "2025-01-15"}
        assert fake_queue.enqueue.call_args.kwargs["job_key"] == "factor_computation:2025-01-15"

    def test_skips_factor_unknown(self, monkeypatch) -> None:
        """FACTOR_UNKNOWN 不应触发补算（配置校验期快速失败兜底）。"""
        db = mock.MagicMock()
        svc = StrategyDecisionService(db=db)
        fake_queue = mock.MagicMock()
        monkeypatch.setattr("quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue)

        context = _make_context()
        svc._context_builder.detect_missing_factors = mock.MagicMock(
            return_value={"typo_factor": MissingReason.FACTOR_UNKNOWN.value}
        )

        triggered = svc.ensure_live_factors(_make_config(), context)

        assert triggered == []
        fake_queue.enqueue.assert_not_called()

    def test_no_enqueue_when_all_factors_ok(self, monkeypatch) -> None:
        """因子全部可用时不应入队。"""
        db = mock.MagicMock()
        svc = StrategyDecisionService(db=db)
        fake_queue = mock.MagicMock()
        monkeypatch.setattr("quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue)

        context = _make_context()
        svc._context_builder.detect_missing_factors = mock.MagicMock(return_value={})

        triggered = svc.ensure_live_factors(_make_config(), context)

        assert triggered == []
        fake_queue.enqueue.assert_not_called()


class TestRunAllocation:
    """统一执行入口的实时分配测试。"""

    def test_run_allocation_delegates_pipeline(self) -> None:
        """run_allocation 应复用统一编排链并返回 AllocationResponse。"""
        db = mock.MagicMock()
        svc = StrategyDecisionService(db=db)
        config = _make_config()
        context = _make_context()

        svc.get_config = mock.MagicMock(return_value=config)
        svc.validate = mock.MagicMock(return_value=StrategyValidationResult(valid=True, errors=[]))
        svc.build_live_context = mock.MagicMock(return_value=context)
        svc.ensure_live_factors = mock.MagicMock(return_value=[])
        fake_result = SimpleNamespace(
            timing=None,
            rankings=[],
            positions={},
            total_exposure=0.0,
            cash_ratio=1.0,
            pipeline_detail=None,
        )
        svc.run = mock.MagicMock(return_value=fake_result)

        resp = svc.run_allocation("s1", trade_date=date(2025, 1, 15))

        assert resp is not None
        assert resp.data_date == date(2025, 1, 15)
        assert resp.plan["method"] == "signal_only"
        svc.ensure_live_factors.assert_called_once_with(config, context)

    def test_run_allocation_raises_on_invalid_config(self) -> None:
        """配置校验失败时快速失败，不继续执行。"""
        db = mock.MagicMock()
        svc = StrategyDecisionService(db=db)
        svc.get_config = mock.MagicMock(return_value=_make_config())
        svc.validate = mock.MagicMock(
            return_value=StrategyValidationResult(valid=False, errors=["未知因子 'x'"])
        )
        svc.build_live_context = mock.MagicMock()

        with mock.patch.object(StrategyDecisionService, "run", mock.MagicMock()) as run_mock:
            try:
                svc.run_allocation("s1", trade_date=date(2025, 1, 15))
            except ValueError:
                pass
            else:
                raise AssertionError("应抛出 ValueError")
            run_mock.assert_not_called()


class TestRunAndPersist:
    """策略运行持久化路径测试。"""

    def test_run_and_persist_writes_via_repos(self) -> None:
        """信号/因子快照写入应统一走仓库写入门禁并标记成功。"""
        db = mock.MagicMock()
        svc = StrategyDecisionService(
            db=db,
            signal_repo=mock.MagicMock(),
            factor_value_repo=mock.MagicMock(),
            run_repo=mock.MagicMock(),
        )
        context = _make_context()
        svc.build_live_context = mock.MagicMock(return_value=context)

        fake_result = SimpleNamespace(
            strategy_results=[
                SimpleNamespace(
                    trade_date=date(2025, 1, 15),
                    etf_code="000300",
                    strategy_id="s1",
                    signal_score=60.0,
                    signal_level="MID",
                    signal_label="中等关注",
                    payload={"target_weight": 0.5},
                    factor_values=[{"factor_id": "return_20d", "value": 3.0}],
                )
            ]
        )
        svc.run = mock.MagicMock(return_value=fake_result)

        svc.run_and_persist(_make_config(), date(2025, 1, 15), "run1")

        svc._signal_repo.delete_by_strategy_date.assert_called_once_with("s1", date(2025, 1, 15))
        svc._factor_value_repo.delete_strategy_values.assert_called_once_with(
            "s1", date(2025, 1, 15)
        )
        svc._signal_repo.bulk_insert.assert_called_once()
        svc._factor_value_repo.bulk_insert_strategy_values.assert_called_once()
        svc._run_repo.mark_success.assert_called_once()
