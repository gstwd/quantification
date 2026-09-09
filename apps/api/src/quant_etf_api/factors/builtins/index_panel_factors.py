"""指数级 RRG/扩散因子（消费行业面板与指数成分面板，输出每指数资产值）。

两个因子把申万行业与个股数据“吸收”为作用在每个指数资产上的普通因子：
- index_diffusion_ratio：指数成分股上涨占比（个股收盘 → 每指数扩散）；
- rrg_industry_match_score：内部先对行业面板做研报信号选择，再按指数
  成分股的申万行业暴露计算与选中行业集合的重合匹配分。

数据输入通过 FactorContext.panels 注入（由实时/回测两侧的因子数据
装配层按 required_data 组装），计算机本身保持纯函数，不直接访问 DB。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from quant_etf_api.factors.base import (
    FactorContext,
    FactorSpec,
    FactorValue,
    USAGE_FILTER,
    USAGE_RANK,
    USAGE_SCORE,
)


def _default_diffusion_params() -> dict[str, Any]:
    """返回指数扩散默认参数（与研报默认口径一致）。"""
    return {
        "diffusion_lookback": 220,
        "smooth_window": 20,
    }


def _default_rrg_match_params() -> dict[str, Any]:
    """返回 RRG 行业匹配默认参数（与行业轮动研报复刻口径一致）。"""
    return {
        "lookback_ratio": 220,
        "lookback_mom": 60,
        "smooth_window": 20,
        "signal": "diffusion_rrg",
        "top_n": 6,
        "keep_quadrants": [1, 2],
        "benchmark_exclude": ["801230"],
        "diffusion_lookback": 220,
    }


def _asof_members(
    membership: dict[str, Any],
    index_code: str,
    trade_date: date,
) -> list[dict[str, Any]]:
    """返回指数在 trade_date 当日的成分列表（含 stock_code/weight）。"""
    rows = membership.get(index_code) or {}
    return list(rows.get(trade_date) or [])


_UNMAPPED_INDUSTRY = "__unmapped__"
_CALCULATION_VERSION = "2026-09-rrg-diffusion-strict-v1"


class IndexDiffusionRatioComputer:
    """指数成分扩散占比（每指数独立，默认参数 220/20）。"""

    @property
    def spec(self) -> FactorSpec:
        """返回指数成分扩散的因子元数据。"""
        return FactorSpec(
            factor_id="index_diffusion_ratio",
            name="指数成分扩散占比",
            category="breadth",
            version="1.0.0",
            description=(
                "指数成分股 close_t > close_{t-lookback} 的数量占比按平滑窗口"
                "取 MA（0-100）。无成分覆盖或样本不足返回 None。"
            ),
            required_data=["index_membership", "stock_closes"],
            lookback_days=520,
            asset_domain="index",
            value_shape="asset",
            usage=[USAGE_SCORE, USAGE_FILTER, USAGE_RANK],
            default_params=_default_diffusion_params(),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单日指数成分扩散占比。"""
        dates = list(ctx.panels.get("calculation_dates") or [trade_date])
        values = self.compute_batch(index_code, dates, ctx)
        return values.get(trade_date, FactorValue(factor_id=self.spec.factor_id, numeric=None))

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算多个交易日的指数成分扩散占比。"""
        params = self.spec.default_params
        lookback = int(params["diffusion_lookback"])
        smooth = int(params["smooth_window"])
        closes = ctx.panels.get("stock_closes") or {}
        membership = ctx.panels.get("index_membership") or {}

        ordered_dates = sorted(set(dates))
        position = {trade_date: i for i, trade_date in enumerate(ordered_dates)}
        ratio_by_date: dict[date, float | None] = {}
        diagnostics: dict[date, dict[str, int | float | None]] = {}
        for trade_date in ordered_dates:
            members = _asof_members(membership, index_code, trade_date)
            member_count = len(members)
            valid = 0
            rising = 0
            if not members:
                ratio_by_date[trade_date] = None
                diagnostics[trade_date] = {
                    "member_count": 0,
                    "valid_sample_count": 0,
                    "missing_sample_count": 0,
                    "rising_sample_count": 0,
                    "raw_ratio": None,
                }
                continue
            base_date = (
                ordered_dates[position[trade_date] - lookback]
                if position[trade_date] >= lookback
                else None
            )
            for member in members:
                code = member["stock_code"]
                series = closes.get(code)
                if not series or base_date is None:
                    continue
                # 必须精确命中交易日，禁止停牌/缺口时回退到上一条可用收盘。
                latest = series.get(trade_date)
                base = series.get(base_date)
                if latest is None or base is None:
                    continue
                valid += 1
                if latest > base:
                    rising += 1
            ratio_by_date[trade_date] = rising / valid if valid > 0 else None
            diagnostics[trade_date] = {
                "member_count": member_count,
                "valid_sample_count": valid,
                "missing_sample_count": member_count - valid,
                "rising_sample_count": rising,
                "raw_ratio": ratio_by_date[trade_date],
            }

        result: dict[date, FactorValue] = {}
        for i, trade_date in enumerate(ordered_dates):
            window_dates = ordered_dates[max(0, i - smooth + 1) : i + 1]
            window = [ratio_by_date[day] for day in window_dates]
            window_complete = len(window) == smooth and all(value is not None for value in window)
            numeric = round(sum(window) / len(window) * 100.0, 4) if window_complete else None
            current = diagnostics[trade_date]
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=numeric,
                payload={
                    **current,
                    "valid_days": sum(value is not None for value in window),
                    "window_complete": window_complete,
                    "lookback": lookback,
                    "smooth_window": smooth,
                    "calculation_version": _CALCULATION_VERSION,
                },
            )
        return result


class RRGIndustryMatchComputer:
    """RRG 行业轮动匹配度（指数成分行业暴露与选中行业集合的重合占比）。"""

    @property
    def spec(self) -> FactorSpec:
        """返回 RRG 行业匹配的因子元数据。"""
        return FactorSpec(
            factor_id="rrg_industry_match_score",
            name="RRG行业轮动匹配度",
            category="relative_strength",
            version="1.0.0",
            description=(
                "先在全部活跃申万一级行业上按研报信号（默认 diffusion_rrg）"
                "选出目标行业集合，再按指数成分股的申万行业暴露计算与选中"
                "集合的重合占比（0-100）。行业面板 warm-up 不足或无成分/"
                "行业归属数据时返回 None。"
            ),
            required_data=[
                "industry_selection",
                "index_industry_exposure",
            ],
            # 行业选择直接消费已预计算的 industry_factor_value 面板，
            # 不要求引擎为本因子扩大指数行情回望窗口
            lookback_days=90,
            asset_domain="index",
            value_shape="asset",
            usage=[USAGE_SCORE, USAGE_FILTER, USAGE_RANK],
            default_params=_default_rrg_match_params(),
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算单日 RRG 行业轮动匹配度。"""
        dates = list(ctx.panels.get("calculation_dates") or [trade_date])
        values = self.compute_batch(index_code, dates, ctx)
        return values.get(trade_date, FactorValue(factor_id=self.spec.factor_id, numeric=None))

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算多个交易日的 RRG 行业轮动匹配度。"""
        selections = ctx.panels.get("industry_selection") or {}
        exposures = ctx.panels.get("index_industry_exposure") or {}
        exposure_meta = ctx.panels.get("index_industry_exposure_meta") or {}
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            selected = selections.get(trade_date) or {}
            exposure = (exposures.get(index_code) or {}).get(trade_date) or {}
            meta = (exposure_meta.get(index_code) or {}).get(trade_date) or {}
            if not selected or not exposure:
                continue
            total = sum(value for value in exposure.values() if value is not None)
            if total <= 0:
                continue
            matched = sum(
                value
                for industry, value in exposure.items()
                if value is not None and industry in selected
            )
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(matched / total * 100.0, 4),
                payload={
                    "selected_count": len(selected),
                    "matched_exposure": round(matched, 6),
                    "total_exposure": round(total, 6),
                    "member_count": meta.get("member_count"),
                    "mapped_member_count": meta.get("mapped_member_count"),
                    "unmapped_member_count": meta.get("unmapped_member_count"),
                    "industry_coverage": meta.get("industry_coverage"),
                    "weighting_mode": meta.get("weighting_mode"),
                    "calculation_version": _CALCULATION_VERSION,
                },
            )
        return result
