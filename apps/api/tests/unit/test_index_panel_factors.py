"""指数级 RRG/扩散因子计算测试（合成 panels，无 DB）。"""

from __future__ import annotations

from datetime import date, timedelta

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.index_panel_factors import (
    IndexDiffusionRatioComputer,
    RRGIndustryMatchComputer,
)


def _dates(count: int, start: date = date(2024, 1, 2)) -> list[date]:
    """生成从 start 起连续的日期列表（每自然日视为交易日）。"""
    return [start + timedelta(days=i) for i in range(count)]


def _closes_series(base: float, step: float, days: int) -> dict[date, float]:
    """生成单调递增收盘序列（step>0 时全部上涨）。"""
    return {d: base + i * step for i, d in enumerate(_dates(days))}


def test_index_diffusion_ratio_basic() -> None:
    """扩散值 = 成分上涨占比（等权），warm-up 不足返回 None。"""
    days = 240
    dates = _dates(days)
    members = {"stock_a": 0.4, "stock_b": 0.6}
    membership = {
        "000300": {d: [{"stock_code": s, "weight": w} for s, w in members.items()] for d in dates}
    }
    closes = {
        "stock_a": _closes_series(10.0, 0.1, days),
        "stock_b": _closes_series(10.0, -0.05, days),
    }
    ctx = FactorContext(
        index_bars={},
        panels={
            "index_membership": membership,
            "stock_closes": closes,
        },
    )
    computer = IndexDiffusionRatioComputer()
    result = computer.compute_batch("000300", dates, ctx)
    # warm-up 不足（前 220 日）无输出
    assert dates[100] not in result
    # 220 日窗口 + 20 日平滑后才首次输出，A 涨 B 跌 → 占比 0.5
    assert dates[238] not in result
    assert result[dates[239]].numeric == 50.0
    assert result[dates[-1]].numeric == 50.0


def test_index_diffusion_ratio_no_membership_none() -> None:
    """无成分覆盖的指数返回 None（调用方按缺失处理）。"""
    dates = _dates(30)
    ctx = FactorContext(index_bars={}, panels={})
    value = IndexDiffusionRatioComputer().compute("399006", dates[-1], ctx)
    assert value.numeric is None


def test_rrg_industry_match_score() -> None:
    """匹配分 = 选中行业暴露占比；暴露含行业 A 与 B。"""
    dates = _dates(5)
    selections = {d: {"801010": 1.0, "801030": 1.0} for d in dates}
    exposures = {
        "000300": {
            d: {"801010": 0.5, "801030": 0.25, "801080": 0.25} for d in dates
        }
    }
    ctx = FactorContext(
        index_bars={},
        panels={
            "industry_selection": selections,
            "index_industry_exposure": exposures,
        },
    )
    computer = RRGIndustryMatchComputer()
    value = computer.compute("000300", dates[0], ctx)
    assert value.numeric is not None
    assert value.numeric == 75.0


def test_rrg_industry_match_missing_exposure_none() -> None:
    """指数缺少行业暴露数据时返回 None。"""
    dates = _dates(5)
    ctx = FactorContext(
        index_bars={},
        panels={
            "industry_selection": {d: {"801010": 1.0} for d in dates},
            "index_industry_exposure": {},
        },
    )
    value = RRGIndustryMatchComputer().compute("000300", dates[0], ctx)
    assert value.numeric is None
