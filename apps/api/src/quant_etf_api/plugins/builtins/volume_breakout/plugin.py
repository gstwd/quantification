from __future__ import annotations

from datetime import date
from typing import Any

from quant_etf_api.plugins.base import StrategyContextData, StrategyResult


def _volume_probability(volume_ratio: float) -> float:
    """将量比分段线性映射为 0~100 的量能概率。

    Args:
        volume_ratio: 20 日量比，即当日成交量 / 近 20 日平均成交量。

    Returns:
        量能概率，0~100。
    """
    if volume_ratio < 0.5:
        return max(0.0, volume_ratio / 0.5 * 5)
    if volume_ratio < 1.0:
        return 5 + (volume_ratio - 0.5) / 0.5 * 12
    if volume_ratio < 1.3:
        return 17 + (volume_ratio - 1.0) / 0.3 * 18
    if volume_ratio < 1.5:
        return 35 + (volume_ratio - 1.3) / 0.2 * 20
    if volume_ratio < 2.0:
        return 55 + (volume_ratio - 1.5) / 0.5 * 25
    if volume_ratio < 3.0:
        return 80 + (volume_ratio - 2.0) / 1.0 * 15
    if volume_ratio < 5.0:
        return 95 + (volume_ratio - 3.0) / 2.0 * 3
    return min(100.0, 98 + (volume_ratio - 5.0) / 5.0 * 2)


def _signal_level(score: float) -> tuple[str, str]:
    """根据综合得分判定信号等级。

    Args:
        score: 综合得分，0~100。

    Returns:
        (等级, 中文标签) 元组。
    """
    if score >= 70:
        return "HIGH", "高确信"
    if score >= 50:
        return "MID", "中等关注"
    return "LOW", "正常"


class VolumeBreakoutDailyPlugin:
    """量能突破策略插件。

    基于量比和单日表现的简单基线策略，使用指数模式。
    """

    strategy_id = "volume_breakout_daily"
    display_name = "量能突破"
    version = "1.0.0"
    frequency = "daily"
    asset_scope = "a_share_etf"
    description = "基于量比和单日表现的简单基线策略。"

    def parameter_schema(self) -> dict[str, Any]:
        """返回策略参数 schema。"""
        return {
            "type": "object",
            "properties": {"min_volume_ratio": {"type": "number", "default": 1.2}},
        }

    def required_inputs(self) -> list[str]:
        """返回所需数据源列表。"""
        return ["index_daily_bar"]

    def factor_definitions(self) -> list[dict[str, Any]]:
        """返回插件定义的因子列表。"""
        return [
            {"factor_id": "volume_ratio_20d", "name": "20日量比"},
            {"factor_id": "volume_breakout_score", "name": "量能突破得分"},
        ]

    def signal_definition(self) -> dict[str, Any]:
        """返回信号定义。"""
        return {"signal_id": "volume_breakout_signal", "name": "量能突破信号"}

    def prepare_context(
        self, trade_date: date, params: dict[str, Any] | None = None
    ) -> StrategyContextData:
        """构建策略上下文（空壳，实际数据由服务层注入）。"""
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
        """对指数宇宙计算量能突破信号。

        Args:
            trade_date: 交易日。
            universe: 指数宇宙列表。
            context: 策略上下文，需包含 extra["etf_bars"] 中的 volume_ratio_20d。
            params: 策略参数。

        Returns:
            StrategyResult 列表。
        """
        results = []
        etf_bars = context.extra.get("etf_bars", {})
        for item in universe:
            code = item["etf_code"]
            ratio = etf_bars.get(code, {}).get("volume_ratio_20d", 1.0)
            score = round(_volume_probability(ratio), 1)
            level, label = _signal_level(score)
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
        """解释单个指数的策略结果。"""
        return {"summary": f"{result.etf_code} 量能突破得分 {result.signal_score}"}
