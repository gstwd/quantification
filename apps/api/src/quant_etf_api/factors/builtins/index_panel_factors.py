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
        values = self.compute_batch(index_code, [trade_date], ctx)
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

        ratio_by_date: dict[date, float] = {}
        for trade_date in dates:
            members = _asof_members(membership, index_code, trade_date)
            if not members:
                continue
            valid = 0
            rising = 0
            for member in members:
                code = member["stock_code"]
                series = closes.get(code)
                if not series:
                    continue
                sorted_dates = sorted(d for d in series if d <= trade_date)
                if len(sorted_dates) < lookback + 1:
                    continue
                latest = series[sorted_dates[-1]]
                base = series[sorted_dates[-lookback - 1]]
                if latest is None or base is None:
                    continue
                valid += 1
                if latest > base:
                    rising += 1
            if valid > 0:
                ratio_by_date[trade_date] = rising / valid

        result: dict[date, FactorValue] = {}
        ordered = [d for d in dates if d in ratio_by_date]
        for i, trade_date in enumerate(ordered):
            window_dates = ordered[max(0, i - smooth + 1) : i + 1]
            window = [ratio_by_date[d] for d in window_dates]
            if len(window) < smooth:
                continue
            avg = sum(window) / len(window)
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(avg * 100.0, 4),
                payload={
                    "valid_days": len(window),
                    "lookback": lookback,
                    "smooth_window": smooth,
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
        values = self.compute_batch(index_code, [trade_date], ctx)
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
        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            selected = selections.get(trade_date) or {}
            exposure = (exposures.get(index_code) or {}).get(trade_date) or {}
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
                },
            )
        return result
