"""ETF 资产配置策略插件。

完整投资决策管线：择时 → 资产轮动 → 仓位分配。
同时兼容旧的 run_for_universe() 模式，输出信号评分供旧回测使用。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from quant_etf_api.domain.strategies.models import (
    AllocationPlan,
    AssetRanking,
    StrategyContextData,
    StrategyResult,
    TimingSignal,
)
from quant_etf_api.plugins.builtins.etf_allocation.rotation import rank_etf_assets
from quant_etf_api.plugins.builtins.etf_allocation.sizing import allocate_positions
from quant_etf_api.plugins.builtins.etf_allocation.timing import assess_timing

logger = logging.getLogger(__name__)


class EtfAllocationPlugin:
    """ETF 资产配置策略插件。

    实现完整的投资决策管线：
    - assess_market_timing：综合估值/趋势/量能判断市场环境
    - rank_assets：按动量 + 估值对 ETF 排名
    - allocate_positions：根据择时 + 排名分配仓位
    - run_for_universe：兼容旧模式，输出信号评分

    择时规则：
    - 估值（PE/PB 百分位）占 40%：低估时倾向进攻
    - 趋势（价格 vs MA60）占 40%：多头时倾向进攻
    - 量能（20 日量比）占 20%：温和放量为佳

    轮动规则：
    - 动量（20 日收益率）占 60%：强者恒强
    - 估值吸引力占 40%：低估值优先

    仓位规则：
    - 进攻：总仓位 80%，前 5 名加权持有
    - 观望：总仓位 50%
    - 防守：总仓位 20%
    - 单只上限 30%
    """

    strategy_id = "etf_allocation"
    display_name = "ETF 资产配置"
    version = "1.0.0"
    frequency = "daily"
    asset_scope = "a_share_etf"
    description = "综合择时、资产轮动、仓位管理的完整投资决策管线。"

    def parameter_schema(self) -> dict[str, Any]:
        """返回策略参数 schema。"""
        return {
            "type": "object",
            "properties": {
                "max_positions": {
                    "type": "integer",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                    "description": "最大持仓 ETF 数量",
                },
            },
        }

    def required_inputs(self) -> list[str]:
        """返回所需数据源列表。"""
        return ["index_daily_bar", "index_valuation"]

    def factor_definitions(self) -> list[dict[str, Any]]:
        """返回插件定义的因子列表。"""
        return [
            {"factor_id": "timing_score", "name": "择时得分"},
            {"factor_id": "timing_regime", "name": "择时状态"},
            {"factor_id": "rotation_score", "name": "轮动得分"},
            {"factor_id": "target_weight", "name": "目标仓位"},
        ]

    def signal_definition(self) -> dict[str, Any]:
        """返回信号定义。"""
        return {
            "signal_id": "allocation_signal",
            "name": "资产配置信号",
            "levels": ["offensive", "neutral", "defensive"],
        }

    def prepare_context(
        self,
        trade_date: date,
        params: dict[str, Any] | None = None,
    ) -> StrategyContextData:
        """构建策略上下文（空壳，实际数据由服务层注入）。"""
        return StrategyContextData(
            benchmark_changes={},
        )

    # ── 决策管线方法 ──────────────────────────────────────────────────────

    def assess_market_timing(
        self,
        trade_date: date,
        context: StrategyContextData,
        params: dict[str, Any] | None = None,
    ) -> TimingSignal:
        """市场择时评估。

        从 context.extra 读取估值和行情数据，综合评估市场环境。

        Args:
            trade_date: 交易日。
            context: 策略上下文，需包含 extra["index_valuation"]、extra["etf_bars"]。
            params: 策略参数（当前未使用）。

        Returns:
            TimingSignal，包含 regime、confidence、label、factors。
        """
        # 从上下文读取数据
        etf_bars = context.extra.get("etf_bars", {})
        index_valuation = context.extra.get("index_valuation", {})
        index_5d_return = context.extra.get("index_5d_return", {})

        # 选取代表性指数估值（沪深300 优先）
        pe_pct: float | None = None
        pb_pct: float | None = None
        for idx_code in ("000300", "000016", "000905"):
            val = index_valuation.get(idx_code, {})
            if val.get("pe_percentile") is not None:
                pe_pct = val["pe_percentile"]
                pb_pct = val.get("pb_percentile")
                break

        # 选取代表性标的的行情（沪深300 优先，兼容 ETF 和指数模式）
        close_price: float | None = None
        ma60: float | None = None
        volume_ratio = 1.0
        for code in ("000300", "510300", "000016", "510050", "000905", "510500"):
            bars = etf_bars.get(code, {})
            if bars.get("close_price") is not None:
                close_price = bars["close_price"]
                ma60 = bars.get("ma60")
                volume_ratio = bars.get("volume_ratio_20d", 1.0)
                break

        # 选取代表性指数 5 日收益
        idx_5d: float | None = None
        for idx_code in ("000300", "000016"):
            if idx_code in index_5d_return:
                idx_5d = index_5d_return[idx_code]
                break

        return assess_timing(
            pe_pct=pe_pct,
            pb_pct=pb_pct,
            close_price=close_price,
            ma60=ma60,
            volume_ratio=volume_ratio,
            index_5d_return=idx_5d,
        )

    def rank_assets(
        self,
        trade_date: date,
        universe: list[dict[str, Any]],
        context: StrategyContextData,
        params: dict[str, Any] | None = None,
    ) -> list[AssetRanking]:
        """资产轮动排名。

        按动量 + 估值对 ETF 宇宙排名。

        Args:
            trade_date: 交易日。
            universe: ETF 宇宙列表。
            context: 策略上下文。
            params: 策略参数（当前未使用）。

        Returns:
            按综合得分降序排列的 AssetRanking 列表。
        """
        etf_bars = context.extra.get("etf_bars", {})
        index_valuation = context.extra.get("index_valuation", {})
        etf_index_map = context.extra.get("etf_index_map", {})

        return rank_etf_assets(
            universe=universe,
            etf_bars=etf_bars,
            index_valuation=index_valuation,
            etf_index_map=etf_index_map,
        )

    def allocate_positions(
        self,
        timing: TimingSignal | None,
        rankings: list[AssetRanking] | None,
        params: dict[str, Any] | None = None,
    ) -> AllocationPlan:
        """仓位分配。

        根据择时信号和资产排名分配仓位。

        Args:
            timing: 择时信号。
            rankings: 资产排名列表。
            params: 策略参数，支持 max_positions。

        Returns:
            AllocationPlan。
        """
        return allocate_positions(timing=timing, rankings=rankings, params=params)

    # ── 兼容旧模式 ────────────────────────────────────────────────────────

    def run_for_universe(
        self,
        trade_date: date,
        universe: list[dict[str, Any]],
        context: StrategyContextData,
        params: dict[str, Any] | None = None,
    ) -> list[StrategyResult]:
        """兼容旧的信号评分模式。

        调用完整决策管线，将结果转换为 StrategyResult 列表。
        择时为 offensive 的 ETF 信号得分高，defensive 的低。

        Args:
            trade_date: 交易日。
            universe: ETF 宇宙列表。
            context: 策略上下文。
            params: 策略参数。

        Returns:
            StrategyResult 列表。
        """
        # 运行完整决策管线
        timing = self.assess_market_timing(trade_date, context, params)
        rankings = self.rank_assets(trade_date, universe, context, params)
        plan = self.allocate_positions(timing, rankings, params)

        # 将排名转换为信号评分
        results: list[StrategyResult] = []
        # 建立排名分映射
        rank_map = {r.etf_code: r for r in (rankings or [])}
        position_map = plan.positions

        for item in universe:
            code = item["etf_code"]
            ranking = rank_map.get(code)
            target_weight = position_map.get(code, 0.0)

            # 信号得分 = 排名得分（0-100），有仓位的加成
            if ranking:
                score = ranking.score
                if target_weight > 0:
                    # 有仓位的 ETF，得分加成
                    score = min(100.0, score + 10)
            else:
                score = 0.0

            # 信号等级
            if timing.regime == "defensive":
                level, label = "LOW", "防守减仓"
            elif target_weight > 0:
                if score >= 70:
                    level, label = "HIGH", "推荐配置"
                else:
                    level, label = "MID", "可选配置"
            else:
                level, label = "LOW", "暂不配置"

            factor_values = [
                {"factor_id": "timing_score", "value": timing.factors.get("composite_score")},
                {"factor_id": "timing_regime", "value": timing.regime},
                {"factor_id": "rotation_score", "value": ranking.score if ranking else None},
                {"factor_id": "target_weight", "value": target_weight},
            ]

            results.append(
                StrategyResult(
                    trade_date=trade_date,
                    etf_code=code,
                    strategy_id=self.strategy_id,
                    signal_score=round(score, 1),
                    signal_level=level,
                    signal_label=label,
                    factor_values=factor_values,
                    payload={
                        "timing_regime": timing.regime,
                        "timing_label": timing.label,
                        "timing_confidence": timing.confidence,
                        "target_weight": target_weight,
                        "total_exposure": plan.total_exposure,
                        "cash_ratio": plan.cash_ratio,
                        "plan_reasoning": plan.reasoning,
                        "momentum_rank": ranking.momentum_rank if ranking else None,
                        "valuation_rank": ranking.valuation_rank if ranking else None,
                        "category": ranking.category if ranking else None,
                    },
                    tags=[ranking.category if ranking else ""],
                )
            )

        return results

    def explain_result(self, result: StrategyResult) -> dict[str, Any]:
        """解释单个 ETF 的策略结果。"""
        return {
            "summary": (
                f"{result.etf_code} 在 {result.payload.get('timing_label', '未知')} 市场环境下，"
                f"目标仓位 {result.payload.get('target_weight', 0):.1%}"
            ),
            "timing": {
                "regime": result.payload.get("timing_regime"),
                "confidence": result.payload.get("timing_confidence"),
            },
            "position": {
                "target_weight": result.payload.get("target_weight"),
                "total_exposure": result.payload.get("total_exposure"),
            },
            "ranking": {
                "momentum_rank": result.payload.get("momentum_rank"),
                "valuation_rank": result.payload.get("valuation_rank"),
            },
        }
