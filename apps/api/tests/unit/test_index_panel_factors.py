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
    """MA 扩散快线为等权多头成分占比，双层平滑完成前返回 None。"""
    days = 333
    dates = _dates(days)
    members = ["stock_a", "stock_b"]
    membership = {
        "000300": {d: [{"stock_code": stock_code} for stock_code in members] for d in dates}
    }
    closes = {
        "stock_a": _closes_series(10.0, 0.1, days),
        "stock_b": _closes_series(10.0, -0.05, days),
    }
    ctx = FactorContext(
        index_bars={},
        panels={
            "calculation_dates": dates,
            "index_membership": membership,
            "stock_closes": closes,
        },
    )
    computer = IndexDiffusionRatioComputer()
    result = computer.compute_batch("000300", dates, ctx)
    # 160 日趋势均线、150 日快线、25 日慢线完成前均保留带诊断信息的空值。
    assert result[dates[100]].numeric is None
    assert result[dates[331]].numeric == 50.0
    assert result[dates[331]].payload["slow_window_complete"] is False
    # A 站上 160 日均线、B 位于均线下方，等权快线为 50%。
    assert result[dates[-1]].numeric == 50.0
    assert result[dates[-1]].payload["slow_value"] == 50.0
    assert result[dates[-1]].payload["bullish"] is False


def test_index_diffusion_excludes_missing_current_close_from_denominator() -> None:
    """当日缺收盘成分不计分子和分母，2 多头 / 8 有效样本应为 25%。"""
    dates = _dates(333)
    members = [f"stock_{i}" for i in range(10)]
    membership = {"000300": {d: [{"stock_code": code} for code in members] for d in dates}}
    closes = {
        code: _closes_series(10.0, 0.1 if index < 2 else -0.1, len(dates))
        for index, code in enumerate(members)
    }
    for code in members[-2:]:
        for trade_date in dates[-20:]:
            closes[code].pop(trade_date)
    ctx = FactorContext(
        index_bars={},
        panels={
            "calculation_dates": dates,
            "index_membership": membership,
            "stock_closes": closes,
        },
    )

    result = IndexDiffusionRatioComputer().compute("000300", dates[-1], ctx)

    # 快线包含最近 150 日：其中最后 20 日为 25%，此前 130 日为 20%。
    assert result.numeric == 20.6667
    assert result.payload["member_count"] == 10
    assert result.payload["valid_sample_count"] == 8
    assert result.payload["missing_sample_count"] == 2
    assert result.payload["bullish_sample_count"] == 2
    assert result.payload["raw_ratio"] == 0.25
    assert result.payload["fast_window_complete"] is True
    assert result.payload["slow_window_complete"] is True


def test_index_diffusion_missing_close_invalidates_smoothing_window() -> None:
    """快线窗口内任一日无有效样本时不得跳日压缩计算。"""
    dates = _dates(333)
    membership = {"000300": {d: [{"stock_code": "stock_a"}] for d in dates}}
    closes = {"stock_a": _closes_series(10.0, 0.1, len(dates))}
    closes["stock_a"].pop(dates[-10])
    ctx = FactorContext(
        index_bars={},
        panels={
            "calculation_dates": dates,
            "index_membership": membership,
            "stock_closes": closes,
        },
    )

    result = IndexDiffusionRatioComputer().compute("000300", dates[-1], ctx)

    assert result.numeric is None
    assert result.payload["fast_window_complete"] is False


def test_index_diffusion_prefers_complete_index_weights() -> None:
    """成分权重齐全时，扩散比例按指数权重而非股票数量聚合。"""
    dates = _dates(333)
    membership = {
        "000300": {
            d: [
                {"stock_code": "stock_a", "weight": 0.2},
                {"stock_code": "stock_b", "weight": 0.8},
            ]
            for d in dates
        }
    }
    ctx = FactorContext(
        index_bars={},
        panels={
            "calculation_dates": dates,
            "index_membership": membership,
            "stock_closes": {
                "stock_a": _closes_series(10.0, 0.1, len(dates)),
                "stock_b": _closes_series(10.0, -0.1, len(dates)),
            },
        },
    )

    value = IndexDiffusionRatioComputer().compute("000300", dates[-1], ctx)

    assert value.numeric == 20.0
    assert value.payload["weighting_mode"] == "index_weight"


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
    exposures = {"000300": {d: {"801010": 0.5, "801030": 0.25, "801080": 0.25} for d in dates}}
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


def test_rrg_industry_match_unmapped_member_stays_in_denominator() -> None:
    """未映射行业成分不能命中，但必须保留在匹配分母。"""
    trade_date = _dates(1)[0]
    ctx = FactorContext(
        index_bars={},
        panels={
            "industry_selection": {trade_date: {"801010": 1.0}},
            "index_industry_exposure": {
                "000300": {trade_date: {"801010": 0.5, "__unmapped__": 0.5}}
            },
            "index_industry_exposure_meta": {
                "000300": {
                    trade_date: {
                        "member_count": 2,
                        "mapped_member_count": 1,
                        "unmapped_member_count": 1,
                        "industry_coverage": 0.5,
                        "weighting_mode": "equal",
                    }
                }
            },
        },
    )

    value = RRGIndustryMatchComputer().compute("000300", trade_date, ctx)

    assert value.numeric == 50.0
    assert value.payload["unmapped_member_count"] == 1
    assert value.payload["industry_coverage"] == 0.5


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
