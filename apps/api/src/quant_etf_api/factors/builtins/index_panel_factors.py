"""指数级 RRG/扩散因子（消费行业面板与指数成分面板，输出每指数资产值）。

两个因子把申万行业与个股数据“吸收”为作用在每个指数资产上的普通因子：
- index_diffusion_ratio：指数成分股站上长期均线的占比（个股收盘 → 每指数扩散）；
- rrg_industry_match_score：内部先对行业面板做研报信号选择，再按指数
  成分股的申万行业暴露计算与选中行业集合的重合匹配分。

数据输入通过 FactorContext.panels 注入（由实时/回测两侧的因子数据
装配层按 required_data 组装），计算机本身保持纯函数，不直接访问 DB。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import logging

from quant_etf_api.factors.base import (
    FactorContext,
    FactorSpec,
    FactorValue,
    USAGE_FILTER,
    USAGE_RANK,
    USAGE_SCORE,
)

logger = logging.getLogger(__name__)


def _default_diffusion_params() -> dict[str, Any]:
    """返回指数扩散默认参数（与扩散指标研究的 MA 口径一致）。"""
    return {
        "trend_window": 160,
        "fast_window": 150,
        "slow_window": 25,
        "weighting_mode": "index_weight",
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
_INDEX_DIFFUSION_CALCULATION_VERSION = "2026-09-index-diffusion-ma-v2"
_RRG_MATCH_CALCULATION_VERSION = "2026-09-rrg-diffusion-strict-v1"


class IndexDiffusionRatioComputer:
    """指数成分 MA 扩散占比（默认 160/150/25，成分权重优先）。"""

    @property
    def spec(self) -> FactorSpec:
        """返回指数成分扩散的因子元数据。"""
        return FactorSpec(
            factor_id="index_diffusion_ratio",
            name="指数成分扩散占比",
            category="breadth",
            version="2.0.0",
            description=(
                "指数成分股收盘价站上 160 日均线的广度，先取 150 日快线，再对"
                "快线取 25 日慢线；numeric 为快线（0-100），payload 提供慢线与"
                "多头交叉状态。成分权重齐全时按指数权重加权，否则等权。"
            ),
            required_data=["index_membership", "stock_closes"],
            lookback_days=650,
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
        trend_window = int(params["trend_window"])
        fast_window = int(params["fast_window"])
        slow_window = int(params["slow_window"])
        weighting_mode = str(params["weighting_mode"])
        closes = ctx.panels.get("stock_closes") or {}
        membership = ctx.panels.get("index_membership") or {}

        logger.debug(
            "扩散因子开始计算: index=%s dates=%d trend=%d fast=%d slow=%d closes=%d",
            index_code,
            len(ordered_dates := sorted(set(dates))),
            trend_window,
            fast_window,
            slow_window,
            len(closes),
        )
        position = {trade_date: i for i, trade_date in enumerate(ordered_dates)}
        ratio_by_date: dict[date, float | None] = {}
        diagnostics: dict[date, dict[str, int | float | None]] = {}
        for trade_date in ordered_dates:
            members = _asof_members(membership, index_code, trade_date)
            member_count = len(members)
            valid = 0
            bullish = 0
            if not members:
                ratio_by_date[trade_date] = None
                diagnostics[trade_date] = {
                    "member_count": 0,
                    "valid_sample_count": 0,
                    "missing_sample_count": 0,
                    "bullish_sample_count": 0,
                    "raw_ratio": None,
                    "weighting_mode": "equal",
                }
                continue
            window_start = position[trade_date] - trend_window + 1
            ma_dates = (
                ordered_dates[window_start : position[trade_date] + 1] if window_start >= 0 else []
            )
            use_index_weight = weighting_mode == "index_weight" and all(
                member.get("weight") is not None and float(member["weight"]) >= 0
                for member in members
            )
            valid_weight = 0.0
            bullish_weight = 0.0
            for member in members:
                code = member["stock_code"]
                series = closes.get(code)
                if not series or len(ma_dates) != trend_window:
                    continue
                # 必须精确命中窗口内每个交易日，禁止停牌/缺口时回退或压缩均线窗口。
                latest = series.get(trade_date)
                history = [series.get(day) for day in ma_dates]
                if latest is None or any(close is None for close in history):
                    continue
                valid += 1
                member_weight = float(member["weight"]) if use_index_weight else 1.0
                valid_weight += member_weight
                if latest > sum(history) / trend_window:
                    bullish += 1
                    bullish_weight += member_weight
            ratio_by_date[trade_date] = bullish_weight / valid_weight if valid_weight > 0 else None
            diagnostics[trade_date] = {
                "member_count": member_count,
                "valid_sample_count": valid,
                "missing_sample_count": member_count - valid,
                "bullish_sample_count": bullish,
                "raw_ratio": ratio_by_date[trade_date],
                "weighting_mode": "index_weight" if use_index_weight else "equal",
            }
            logger.debug(
                "扩散原始占比: index=%s date=%s members=%d valid=%d bullish=%d ratio=%s weighting=%s",
                index_code,
                trade_date,
                member_count,
                valid,
                bullish,
                ratio_by_date[trade_date],
                diagnostics[trade_date]["weighting_mode"],
            )

        fast_by_date: dict[date, float | None] = {}
        fast_valid_days_by_date: dict[date, int] = {}
        for i, trade_date in enumerate(ordered_dates):
            fast_dates = ordered_dates[max(0, i - fast_window + 1) : i + 1]
            fast_values = [ratio_by_date[day] for day in fast_dates]
            fast_complete = len(fast_values) == fast_window and all(
                value is not None for value in fast_values
            )
            fast_by_date[trade_date] = sum(fast_values) / fast_window if fast_complete else None
            fast_valid_days_by_date[trade_date] = sum(value is not None for value in fast_values)

        result: dict[date, FactorValue] = {}
        for i, trade_date in enumerate(ordered_dates):
            fast_value = fast_by_date[trade_date]
            slow_dates = ordered_dates[max(0, i - slow_window + 1) : i + 1]
            slow_values = [fast_by_date[day] for day in slow_dates]
            slow_complete = len(slow_values) == slow_window and all(
                value is not None for value in slow_values
            )
            slow_value = sum(slow_values) / slow_window if slow_complete else None
            numeric = round(fast_value * 100.0, 4) if fast_value is not None else None
            current = diagnostics[trade_date]
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=numeric,
                payload={
                    **current,
                    "fast_value": round(fast_value * 100.0, 4) if fast_value is not None else None,
                    "slow_value": round(slow_value * 100.0, 4) if slow_value is not None else None,
                    "bullish": fast_value > slow_value
                    if slow_value is not None and fast_value is not None
                    else None,
                    "fast_valid_days": fast_valid_days_by_date[trade_date],
                    "fast_window_complete": fast_value is not None,
                    "slow_window_complete": slow_complete,
                    "trend_window": trend_window,
                    "fast_window": fast_window,
                    "slow_window": slow_window,
                    "calculation_version": _INDEX_DIFFUSION_CALCULATION_VERSION,
                },
            )
            logger.debug(
                "扩散因子完成: index=%s date=%s raw=%s fast=%s slow=%s numeric=%s "
                "fast_valid=%d fast_complete=%s slow_complete=%s",
                index_code,
                trade_date,
                current["raw_ratio"],
                fast_value,
                slow_value,
                numeric,
                fast_valid_days_by_date[trade_date],
                fast_value is not None,
                slow_complete,
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
            # 行业选择直接消费面板装配层从原始数据计算出的结果，
            # 不要求引擎为本因子扩大指数行情回望窗口
            lookback_days=90,
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
        logger.debug(
            "RRG匹配开始计算: index=%s dates=%d selection_dates=%d exposure_dates=%d",
            index_code,
            len(dates),
            len(selections),
            len(exposures.get(index_code) or {}),
        )
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            selected = selections.get(trade_date) or {}
            exposure = (exposures.get(index_code) or {}).get(trade_date) or {}
            meta = (exposure_meta.get(index_code) or {}).get(trade_date) or {}
            if not selected or not exposure:
                logger.debug(
                    "RRG匹配缺少输入: index=%s date=%s selected=%d exposure_industries=%d",
                    index_code,
                    trade_date,
                    len(selected),
                    len(exposure),
                )
                continue
            total = sum(value for value in exposure.values() if value is not None)
            if total <= 0:
                logger.debug("RRG匹配暴露总量无效: index=%s date=%s total=%s", index_code, trade_date, total)
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
                    "calculation_version": _RRG_MATCH_CALCULATION_VERSION,
                },
            )
            logger.debug(
                "RRG匹配完成: index=%s date=%s selected=%d matched=%s total=%s score=%s coverage=%s",
                index_code,
                trade_date,
                len(selected),
                matched,
                total,
                result[trade_date].numeric,
                meta.get("industry_coverage"),
            )
        return result
