from __future__ import annotations

from datetime import date
from typing import Any

from quant_etf_api.plugins.base import StrategyContextData, StrategyResult
from quant_etf_api.plugins.builtins.three_factor.factors import share_probability, signal_level


class ShareFlowMonitorPlugin:
    strategy_id = "share_flow_monitor"
    display_name = "份额流监控"
    version = "1.0.0"
    frequency = "daily"
    asset_scope = "a_share_etf"
    description = "关注 ETF 份额变化的单因子研究插件。"

    def parameter_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def required_inputs(self) -> list[str]:
        # 仅依赖份额数据，不需要行情数据
        return ["etf_daily_share"]

    def factor_definitions(self) -> list[dict[str, Any]]:
        return [
            {"factor_id": "share_delta_pct", "name": "份额日变"},
            {"factor_id": "share_prob", "name": "份额概率"},
        ]

    def signal_definition(self) -> dict[str, Any]:
        return {"signal_id": "share_flow_signal", "name": "份额流信号"}

    def prepare_context(
        self, trade_date: date, params: dict[str, Any] | None = None
    ) -> StrategyContextData:
        return StrategyContextData(
            share_changes={},
        )

    def run_for_universe(
        self,
        trade_date: date,
        universe: list[dict[str, Any]],
        context: StrategyContextData,
        params: dict[str, Any] | None = None,
    ) -> list[StrategyResult]:
        results = []
        for item in universe:
            code = item["etf_code"]
            # 东方财富未覆盖的 ETF 份额变化率为 None，share_probability 返回 None 时默认 0
            delta_pct = context.share_changes.get(code, {}).get("share_delta_pct")
            score = float(share_probability(delta_pct) or 0.0)
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
                        {"factor_id": "share_delta_pct", "value": delta_pct},
                        {"factor_id": "share_prob", "value": score},
                    ],
                    payload={"share_delta_pct": delta_pct},
                    tags=["share_flow"],
                )
            )
        return results

    def explain_result(self, result: StrategyResult) -> dict[str, Any]:
        return {"summary": f"{result.etf_code} 份额变化驱动得分 {result.signal_score}"}
