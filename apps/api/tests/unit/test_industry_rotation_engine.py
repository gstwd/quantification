"""行业轮动引擎与编排器分支测试。"""

from __future__ import annotations

from datetime import date

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import PortfolioConfig, RotationConfig, ScoreConfig, StrategyConfig
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.engine.rotation import IndustryRotationEngine, IndustryRotationInput


def _config(signal: str = "diffusion_rrg") -> StrategyConfig:
    """构造最小 rotation 策略配置。"""
    return StrategyConfig(
        strategy_id="industry_rotation_test",
        display_name="行业轮动测试",
        score=ScoreConfig(factors={}),
        portfolio=PortfolioConfig(method="equal_weight", default_exposure=1.0),
        rotation=RotationConfig(
            signal=signal,
            top_n=2,
            keep_quadrants=[1, 2],
        ),
    )


def _input(trade_date: date = date(2024, 1, 2)) -> IndustryRotationInput:
    """构造三个行业、两类象限的输入。"""
    return IndustryRotationInput(
        trade_date=trade_date,
        industry_codes=["801010", "801030", "801050", "801080"],
        rs_ratio={"801010": 110.0, "801030": 90.0, "801050": 115.0, "801080": 95.0},
        rs_momentum={"801010": 110.0, "801030": 110.0, "801050": 95.0, "801080": 85.0},
        quadrant={"801010": 1, "801030": 2, "801050": 4, "801080": 3},
        diffusion={"801010": 0.9, "801030": 0.8, "801050": 0.7, "801080": 0.6},
    )


def test_diffusion_rrg_no_backfill() -> None:
    """信号 C：扩散 top2 为 801010/801030，均保留一/二象限。"""
    config = _config("diffusion_rrg")
    selected, weights = IndustryRotationEngine().select(config.rotation, _input())
    assert set(selected) == {"801010", "801030"}
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_diffusion_rrg_drops_lagging_without_backfill() -> None:
    """扩散 top2 若含滞后行业则剔除后不补足。"""
    data = _input()
    data.diffusion = {"801010": 0.9, "801080": 0.8, "801030": 0.7, "801050": 0.6}
    config = _config("diffusion_rrg")
    selected, weights = IndustryRotationEngine().select(config.rotation, data)
    assert selected == ["801010"]  # 801080 为象限 3，被剔除且不回填
    assert weights == {"801010": 1.0}


def test_quadrant_signal_uses_keep_quadrants() -> None:
    """信号 A：只保留指定象限。"""
    config = _config("quadrant")
    selected, _ = IndustryRotationEngine().select(config.rotation, _input())
    assert set(selected) == {"801010", "801030"}


def test_diffusion_signal_topn() -> None:
    """信号 B：按扩散值取 top_n。"""
    config = _config("diffusion")
    selected, _ = IndustryRotationEngine().select(config.rotation, _input())
    assert set(selected) == {"801010", "801030"}


def test_orchestrator_rotation_branch() -> None:
    """编排器在 rotation 存在时走行业轮动分支。"""
    config = _config()
    context = EngineContext(
        trade_date=date(2024, 1, 2),
        universe=[],
        extra={"industry_rotation_input": _input()},
    )
    result = StrategyEngine().run(config, context, include_details=False)
    assert set(result.positions) == {"801010", "801030"}
    assert abs(result.total_exposure - 1.0) < 1e-6


def test_orchestrator_rotation_missing_input_raises() -> None:
    """缺少行业轮动输入时报清晰错误而非静默空仓。"""
    config = _config()
    context = EngineContext(trade_date=date(2024, 1, 2), universe=[])
    try:
        StrategyEngine().run(config, context, include_details=False)
    except ValueError as e:
        assert "industry_rotation_input" in str(e)
    else:
        raise AssertionError("应抛出 ValueError")
