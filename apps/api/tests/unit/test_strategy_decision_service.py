"""统一策略执行服务测试（C1 收敛点）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import mock

from quant_etf_api.engine.config import ScoreConfig, StrategyConfig
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.factors.base import MissingReason
from quant_etf_api.schemas.strategy import StrategyValidationResult
from quant_etf_api.services.strategy_decision_service import StrategyDecisionService


def _make_config() -> StrategyConfig:
    """构建测试策略配置。"""
    return StrategyConfig(
        strategy_id="s1",
        display_name="测试策略",
        score=ScoreConfig(factors={"close_price": 1.0}),
    )


def _make_context() -> SimpleNamespace:
    """构建带 universe/asset_factors 的伪上下文。"""
    return SimpleNamespace(
        trade_date=date(2025, 1, 15),
        universe=[{"index_code": "000300", "name_cn": "沪深300"}],
        asset_factors={("000300", "close_price"): 100.0},
    )


class TestInsufficientFactorWarnings:
    """实时分配的因子缺失告警测试。"""

    def test_warns_for_insufficient_data(self) -> None:
        """数据不足的因子应产生 MISSING_FACTOR 告警，且不阻塞本次分配。"""
        svc = StrategyDecisionService(db=mock.MagicMock())
        config = _make_config()
        context = _make_context()

        svc.get_config = mock.MagicMock(return_value=config)
        svc.validate = mock.MagicMock(return_value=StrategyValidationResult(valid=True, errors=[]))
        svc.build_live_context = mock.MagicMock(return_value=context)
        svc._context_builder.insufficient_factors = mock.MagicMock(
            return_value={"close_price": MissingReason.INSUFFICIENT_DATA.value}
        )
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
        assert len(resp.warnings) == 1
        assert resp.warnings[0].code == "MISSING_FACTOR"
        assert "数据不足" in resp.warnings[0].message

    def test_warns_for_compute_failure(self) -> None:
        """计算失败的因子告警文案与数据不足区分。"""
        svc = StrategyDecisionService(db=mock.MagicMock())
        svc.get_config = mock.MagicMock(return_value=_make_config())
        svc.validate = mock.MagicMock(return_value=StrategyValidationResult(valid=True, errors=[]))
        svc.build_live_context = mock.MagicMock(return_value=_make_context())
        svc._context_builder.insufficient_factors = mock.MagicMock(
            return_value={"close_price": MissingReason.COMPUTE_FAILED.value}
        )
        svc.run = mock.MagicMock(
            return_value=SimpleNamespace(
                timing=None,
                rankings=[],
                positions={},
                total_exposure=0.0,
                cash_ratio=1.0,
                pipeline_detail=None,
            )
        )

        resp = svc.run_allocation("s1", trade_date=date(2025, 1, 15))

        assert resp is not None
        assert "计算失败" in resp.warnings[0].message

    def test_no_warning_when_factors_ok(self) -> None:
        """因子全部可用时不产生告警。"""
        svc = StrategyDecisionService(db=mock.MagicMock())
        svc.get_config = mock.MagicMock(return_value=_make_config())
        svc.validate = mock.MagicMock(return_value=StrategyValidationResult(valid=True, errors=[]))
        svc.build_live_context = mock.MagicMock(return_value=_make_context())
        svc._context_builder.insufficient_factors = mock.MagicMock(return_value={})
        svc.run = mock.MagicMock(
            return_value=SimpleNamespace(
                timing=None,
                rankings=[],
                positions={},
                total_exposure=0.0,
                cash_ratio=1.0,
                pipeline_detail=None,
            )
        )

        resp = svc.run_allocation("s1", trade_date=date(2025, 1, 15))

        assert resp is not None
        assert resp.warnings == []

    def test_insufficient_factors_reads_engine_context(self) -> None:
        """缺失诊断直接消费引擎上下文，不重新计算因子。"""
        svc = StrategyDecisionService(db=mock.MagicMock())
        context = EngineContext(
            trade_date=date(2025, 1, 15),
            universe=[{"index_code": "000300"}],
            asset_factors={("000300", "close_price"): None},
        )

        assert svc._context_builder.insufficient_factors(context) == {
            "close_price": MissingReason.INSUFFICIENT_DATA.value
        }


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
        svc._context_builder.insufficient_factors = mock.MagicMock(return_value={})
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
        assert resp.plan["method"] == "equal_weight"
        svc.run.assert_called_once_with(config, context)

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

    def test_run_and_persist_writes_signals_only(self) -> None:
        """只持久化信号，不再写入任何因子值快照。"""
        db = mock.MagicMock()
        svc = StrategyDecisionService(
            db=db,
            signal_repo=mock.MagicMock(),
            run_repo=mock.MagicMock(),
        )
        context = _make_context()
        svc.build_live_context = mock.MagicMock(return_value=context)

        fake_result = SimpleNamespace(
            strategy_results=[
                SimpleNamespace(
                    trade_date=date(2025, 1, 15),
                    index_code="000300",
                    strategy_id="s1",
                    signal_score=60.0,
                    signal_level="MID",
                    signal_label="中等关注",
                    payload={"target_weight": 0.5},
                )
            ]
        )
        svc.run = mock.MagicMock(return_value=fake_result)

        svc.run_and_persist(_make_config(), date(2025, 1, 15), "run1")

        svc._signal_repo.delete_by_strategy_date.assert_called_once_with("s1", date(2025, 1, 15))
        svc._signal_repo.bulk_insert.assert_called_once()
        svc._run_repo.mark_success.assert_called_once()
        # 因子值不落库：服务不持有因子值仓库
        assert not hasattr(svc, "_factor_value_repo")
