from __future__ import annotations

from datetime import date
from typing import Any

from quant_etf_api.domain.strategies.scoring import signal_level, volume_probability
from quant_etf_api.plugins.base import StrategyContextData, StrategyResult


class VolumeBreakoutDailyPlugin:
    strategy_id = "volume_breakout_daily"
    display_name = "量能突破"
    version = "1.0.0"
    frequency = "daily"
    asset_scope = "a_share_etf"
    description = "基于量比和单日表现的简单基线策略。"

    def parameter_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"min_volume_ratio": {"type": "number", "default": 1.2}},
        }

    def required_inputs(self) -> list[str]:
        # 仅依赖日频行情数据，是最轻量的基线策略
        return ["etf_daily_bar"]

    def factor_definitions(self) -> list[dict[str, Any]]:
        return [
            {"factor_id": "volume_ratio_20d", "name": "20日量比"},
            {"factor_id": "volume_breakout_score", "name": "量能突破得分"},
        ]

    def signal_definition(self) -> dict[str, Any]:
        return {"signal_id": "volume_breakout_signal", "name": "量能突破信号"}

    def prepare_context(
        self, trade_date: date, params: dict[str, Any] | None = None
    ) -> StrategyContextData:
        return StrategyContextData(
            extra={},
        )

    def run_for_universe(
        self,
        trade_date: date,
        universe: list[dict[str, Any]],
        context: StrategyContextData,
        params: dict[str, Any] | None = None,
    ) -> list[StrategyResult]:
        results = []
        # 优先从 context.extra["etf_bars"] 读取真实历史量比（回测场景），无数据时回退到 stub
        etf_bars = context.extra.get("etf_bars", {})
        stub_ratios = context.extra.get(
            "volume_ratios", {"510300": 1.92, "510050": 1.28, "510500": 0.88, "159919": 1.57}
        )
        for item in universe:
            code = item["etf_code"]
            # 未知 ETF 默认量比 1.0（平量）
            ratio = etf_bars.get(code, {}).get("volume_ratio_20d") or stub_ratios.get(code, 1.0)
            score = round(volume_probability(ratio), 1)
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
                        {"factor_id": "volume_ratio_20d", "value": ratio},
                        {"factor_id": "volume_breakout_score", "value": score},
                    ],
                    payload={"volume_ratio": ratio},
                    tags=["volume_breakout"],
                )
            )
        return results

    def explain_result(self, result: StrategyResult) -> dict[str, Any]:
        return {"summary": f"{result.etf_code} 量能突破得分 {result.signal_score}"}
