from __future__ import annotations

from datetime import date

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

    def parameter_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"lookback_days": {"type": "integer", "default": 20, "minimum": 20}},
        }

    def required_inputs(self) -> list[str]:
        return ["etf_daily_bar", "index_daily_bar", "etf_daily_share"]

    def factor_definitions(self) -> list[dict]:
        return [
            {"factor_id": "volume_ratio_20d", "name": "20日量比"},
            {"factor_id": "volume_prob", "name": "量能概率"},
            {"factor_id": "direction_prob", "name": "方向概率"},
            {"factor_id": "share_prob", "name": "份额概率"},
            {"factor_id": "composite_prob", "name": "综合概率"},
        ]

    def signal_definition(self) -> dict:
        return {"signal_id": "three_factor_signal", "name": "三因子综合信号"}

    def prepare_context(self, trade_date: date, params: dict | None = None) -> StrategyContextData:
        return StrategyContextData(
            benchmark_changes={"000300": -0.42},
            share_changes={
                "510300": {"share_delta_pct": 1.8},
                "510050": {"share_delta_pct": 0.6},
                "510500": {"share_delta_pct": -0.4},
                "159919": {"share_delta_pct": 2.2},
            },
        )

    def run_for_universe(self, trade_date: date, universe: list[dict], context: StrategyContextData, params: dict | None = None) -> list[StrategyResult]:
        results: list[StrategyResult] = []
        base_volume_ratios = {
            "510300": 1.92,
            "510050": 1.28,
            "510500": 0.88,
            "159919": 1.57,
        }
        base_change_pct = {"510300": 0.8, "510050": 0.4, "510500": -0.2, "159919": 0.9}
        base_etf_5d = {"510300": 1.6, "510050": 0.5, "510500": -1.2, "159919": 1.8}
        index_change = context.benchmark_changes.get("000300", 0.0)
        index_5d = -1.4

        for item in universe:
            code = item["etf_code"]
            volume_ratio = base_volume_ratios.get(code, 1.0)
            change_pct = base_change_pct.get(code, 0.0)
            etf_5d = base_etf_5d.get(code, 0.0)
            volume_prob = round(volume_probability(volume_ratio), 1)
            direction_prob_value = direction_probability(change_pct, etf_5d, index_5d, volume_ratio, index_change)
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

    def explain_result(self, result: StrategyResult) -> dict:
        return {
            "summary": f"{result.etf_code} 的三因子综合得分为 {result.signal_score}",
            "payload": result.payload,
        }
