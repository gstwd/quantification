from datetime import date

from quant_etf_api.plugins.base import StrategyContextData, StrategyResult
from quant_etf_api.plugins.builtins.three_factor.factors import signal_level, volume_probability


class VolumeBreakoutDailyPlugin:
    strategy_id = "volume_breakout_daily"
    display_name = "量能突破"
    version = "1.0.0"
    frequency = "daily"
    asset_scope = "a_share_etf"
    description = "基于量比和单日表现的简单基线策略。"

    def parameter_schema(self) -> dict:
        return {"type": "object", "properties": {"min_volume_ratio": {"type": "number", "default": 1.2}}}

    def required_inputs(self) -> list[str]:
        return ["etf_daily_bar"]

    def factor_definitions(self) -> list[dict]:
        return [{"factor_id": "volume_ratio_20d", "name": "20日量比"}, {"factor_id": "volume_breakout_score", "name": "量能突破得分"}]

    def signal_definition(self) -> dict:
        return {"signal_id": "volume_breakout_signal", "name": "量能突破信号"}

    def prepare_context(self, trade_date: date, params: dict | None = None) -> StrategyContextData:
        return StrategyContextData(extra={"volume_ratios": {"510300": 1.92, "510050": 1.28, "510500": 0.88, "159919": 1.57}})

    def run_for_universe(self, trade_date: date, universe: list[dict], context: StrategyContextData, params: dict | None = None) -> list[StrategyResult]:
        results = []
        ratios = context.extra.get("volume_ratios", {})
        for item in universe:
            code = item["etf_code"]
            ratio = ratios.get(code, 1.0)
            score = round(volume_probability(ratio), 1)
            level, label = signal_level(score)
            results.append(StrategyResult(trade_date=trade_date, etf_code=code, strategy_id=self.strategy_id, signal_score=score, signal_level=level, signal_label=label, factor_values=[{"factor_id": "volume_ratio_20d", "value": ratio}, {"factor_id": "volume_breakout_score", "value": score}], payload={"volume_ratio": ratio}, tags=["volume_breakout"]))
        return results

    def explain_result(self, result: StrategyResult) -> dict:
        return {"summary": f"{result.etf_code} 量能突破得分 {result.signal_score}"}
