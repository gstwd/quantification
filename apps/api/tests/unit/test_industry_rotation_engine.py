"""行业轮动纯选择引擎测试（领域层，供研究工作台与指数级因子复用）。"""

from __future__ import annotations

from datetime import date

from quant_etf_api.domain.industry.selection import (
    IndustryRotationEngine,
    IndustryRotationInput,
    IndustrySelectionConfig,
)


def _config(signal: str = "diffusion_rrg") -> IndustrySelectionConfig:
    """构造最小行业选择参数。"""
    return IndustrySelectionConfig(
        signal=signal,
        top_n=2,
        keep_quadrants=[1, 2],
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
    selected, weights = IndustryRotationEngine().select(config, _input())
    assert set(selected) == {"801010", "801030"}
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_diffusion_rrg_drops_lagging_without_backfill() -> None:
    """扩散 top2 若含滞后行业则剔除后不补足。"""
    data = _input()
    data.diffusion = {"801010": 0.9, "801080": 0.8, "801030": 0.7, "801050": 0.6}
    config = _config("diffusion_rrg")
    selected, weights = IndustryRotationEngine().select(config, data)
    assert selected == ["801010"]  # 801080 为象限 3，被剔除且不回填
    assert weights == {"801010": 1.0}


def test_quadrant_signal_uses_keep_quadrants() -> None:
    """信号 A：只保留指定象限。"""
    config = _config("quadrant")
    selected, _ = IndustryRotationEngine().select(config, _input())
    assert set(selected) == {"801010", "801030"}


def test_diffusion_signal_topn() -> None:
    """信号 B：按扩散值取 top_n。"""
    config = _config("diffusion")
    selected, _ = IndustryRotationEngine().select(config, _input())
    assert set(selected) == {"801010", "801030"}
