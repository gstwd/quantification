from __future__ import annotations

from datetime import date
from typing import Any

from quant_etf_api.plugins.base import StrategyContextData, StrategyResult
from quant_etf_api.plugins.builtins.three_factor.factors import (
    composite_probability,
    direction_probability,
    share_probability,
    signal_level,
    volume_probability,
)


class ThreeFactorGuardPlugin:
    strategy_id = "three_factor_guard"
    display_name = "三因子监控"
    version = "1.0.0"
    frequency = "daily"
    asset_scope = "a_share_etf"
    description = "基于量能、方向、份额三个因子的 A 股 ETF 日频研究信号。"

    def parameter_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"lookback_days": {"type": "integer", "default": 20, "minimum": 20}},
        }

    def required_inputs(self) -> list[str]:
        return ["etf_daily_bar", "index_daily_bar", "etf_daily_share"]

    def factor_definitions(self) -> list[dict[str, Any]]:
        return [
            {"factor_id": "volume_ratio_20d", "name": "20日量比"},
            {"factor_id": "volume_prob", "name": "量能概率"},
            {"factor_id": "direction_prob", "name": "方向概率"},
            {"factor_id": "share_prob", "name": "份额概率"},
            {"factor_id": "composite_prob", "name": "综合概率"},
        ]

    def signal_definition(self) -> dict[str, Any]:
        return {"signal_id": "three_factor_signal", "name": "三因子综合信号"}

    def prepare_context(
        self, trade_date: date, params: dict[str, Any] | None = None
    ) -> StrategyContextData:
        return StrategyContextData(
            benchmark_changes={},
            share_changes={},
        )

    def run_for_universe(
        self,
        trade_date: date,
        universe: list[dict[str, Any]],
        context: StrategyContextData,
        params: dict[str, Any] | None = None,
    ) -> list[StrategyResult]:
        results: list[StrategyResult] = []
        # 模拟数据：回测时由 BacktestService 通过 context.extra["etf_bars"] 注入真实历史数据
        base_volume_ratios = {
            "510300": 1.92,
            "510050": 1.28,
            "510500": 0.88,
            "159919": 1.57,
        }
        base_change_pct = {"510300": 0.8, "510050": 0.4, "510500": -0.2, "159919": 0.9}
        base_etf_5d = {"510300": 1.6, "510050": 0.5, "510500": -1.2, "159919": 1.8}
        index_change = context.benchmark_changes.get("000300", 0.0)
        # 优先从 context.extra 读取真实指数 5 日收益，无数据时使用模拟值
        index_5d = context.extra.get("index_5d_return", {}).get("000300", -1.4)
        etf_bars = context.extra.get("etf_bars", {})

        for item in universe:
            code = item["etf_code"]
            bar_data = etf_bars.get(code, {})
            # 优先使用回测注入的真实历史数据，无数据时回退到内置 stub
            volume_ratio = bar_data.get("volume_ratio_20d") or base_volume_ratios.get(code, 1.0)
            change_pct = (
                bar_data.get("change_pct")
                if bar_data.get("change_pct") is not None
                else base_change_pct.get(code, 0.0)
            )
            etf_5d = (
                bar_data.get("etf_5d_return")
                if bar_data.get("etf_5d_return") is not None
                else base_etf_5d.get(code, 0.0)
            )
            volume_prob = round(volume_probability(volume_ratio), 1)
            direction_prob_value = direction_probability(
                change_pct, etf_5d, index_5d, volume_ratio, index_change
            )
            # 从上下文取份额变化率，东方财富未覆盖的 ETF 返回 None
            share_delta_pct = context.share_changes.get(code, {}).get("share_delta_pct")
            share_prob_value = share_probability(share_delta_pct)
            score = composite_probability(volume_prob, direction_prob_value, share_prob_value)
            level, label = signal_level(score)
            results.append(
                StrategyResult(
                    trade_date=trade_date,
                    etf_code=code,
                    strategy_id=self.strategy_id,
                    signal_score=score,
                    signal_level=level,
                    signal_label=label,
                    factor_values=[
                        {"factor_id": "volume_ratio_20d", "value": volume_ratio},
                        {"factor_id": "volume_prob", "value": volume_prob},
                        {"factor_id": "direction_prob", "value": direction_prob_value},
                        {"factor_id": "share_prob", "value": share_prob_value},
                        {"factor_id": "composite_prob", "value": score},
                    ],
                    payload={
                        "change_pct": change_pct,
                        "etf_5d": etf_5d,
                        "index_5d": index_5d,
                        "index_change_pct": index_change,
                        "share_delta_pct": share_delta_pct,
                    },
                    tags=[item.get("tracking_index_name", "")],
                )
            )
        return results

    def explain_result(self, result: StrategyResult) -> dict[str, Any]:
        return {
            "summary": f"{result.etf_code} 的三因子综合得分为 {result.signal_score}",
            "payload": result.payload,
        }
