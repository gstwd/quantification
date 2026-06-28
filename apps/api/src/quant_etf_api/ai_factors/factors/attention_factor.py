"""AI 关注度因子计算器。

关注度因子衡量特定指数/行业的新闻曝光热度，
高关注度往往伴随高波动或多空分歧。
"""

from __future__ import annotations

from datetime import date, timedelta

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


class Attention1dComputer:
    """1 日关注度因子计算器。

    从 ai_sentiment 上下文中读取对应指数的当日总关注度分。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 1 日关注度因子的元数据。"""
        return FactorSpec(
            factor_id="ai_attention_1d",
            name="AI关注度因子(1日)",
            category="attention",
            version="1.0.0",
            description="基于新闻热榜排名和出现频次的指数关注度得分（1 日），反映当日曝光热度。",
            required_data=["ai_sentiment"],
            lookback_days=7,
        )

    def compute(
        self,
        index_code: str,
        trade_date: date,
        ctx: FactorContext,
    ) -> FactorValue:
        """计算 1 日关注度因子的值。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: 因子上下文。

        Returns:
            FactorValue。
        """
        agg = ctx.ai_sentiment.get((index_code, trade_date))
        if agg is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": f"{index_code} 在 {trade_date} 无 AI 关注度数据"},
            )

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(agg.total_attention, 2),
            payload={
                "news_count": agg.news_count,
                "positive_ratio": agg.positive_ratio,
                "negative_ratio": agg.negative_ratio,
            },
        )


class Attention5dComputer:
    """5 日关注度变化因子计算器。

    计算 5 日均关注度相对于前 5 日均值的变化率，
    正值表示关注度上升，负值表示降温。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 5 日关注度因子的元数据。"""
        return FactorSpec(
            factor_id="ai_attention_5d",
            name="AI关注度因子(5日变化)",
            category="attention",
            version="1.0.0",
            description="新闻关注度的 5 日均值变化率（%），正值=关注度上升，负值=降温。",
            required_data=["ai_sentiment"],
            lookback_days=21,
        )

    def compute(
        self,
        index_code: str,
        trade_date: date,
        ctx: FactorContext,
    ) -> FactorValue:
        """计算 5 日关注度变化率。

        取最近 5 天的平均关注度（近期），与再往前 5 天的平均关注度（基期）对比，
        计算变化率（%）。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: 因子上下文。

        Returns:
            FactorValue，基期数据不足时返回 None。
        """
        recent: list[float] = []
        base: list[float] = []

        for delta in range(5):
            check_date = trade_date - timedelta(days=delta)
            agg = ctx.ai_sentiment.get((index_code, check_date))
            if agg is not None:
                recent.append(agg.total_attention)

        for delta in range(5, 10):
            check_date = trade_date - timedelta(days=delta)
            agg = ctx.ai_sentiment.get((index_code, check_date))
            if agg is not None:
                base.append(agg.total_attention)

        if len(recent) < 3 or len(base) < 2:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "reason": f"近期 {len(recent)}/5 天，基期 {len(base)}/5 天，数据不足",
                },
            )

        recent_avg = sum(recent) / len(recent)
        base_avg = sum(base) / len(base)

        if base_avg < 0.01:
            # 基期关注度几乎为零
            change_pct = 100.0 if recent_avg > 1 else 0.0
        else:
            change_pct = ((recent_avg - base_avg) / base_avg) * 100

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(change_pct, 2),
            payload={
                "recent_avg": round(recent_avg, 2),
                "base_avg": round(base_avg, 2),
                "recent_days": len(recent),
                "base_days": len(base),
            },
        )
