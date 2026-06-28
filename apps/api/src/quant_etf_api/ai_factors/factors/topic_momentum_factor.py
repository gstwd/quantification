"""AI 主题动量子计算器。

衡量特定主题/行业的新闻热度的动量变化，
类似价格动量，但基于新闻关注度的时序变化。
"""

from __future__ import annotations

from datetime import date, timedelta

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


class TopicMomentumComputer:
    """主题动量子计算器。

    比较当期（1-3 日）与基期（4-6 日）的关注度变化率，
    正值表示主题正在升温（动量向上），负值表示退潮。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回主题动量的因子元数据。"""
        return FactorSpec(
            factor_id="ai_topic_momentum",
            name="AI主题动因子",
            category="attention",
            version="1.0.0",
            description="基于新闻关注度时序变化的主题热度动量（%），正值=升温，负值=退潮。",
            required_data=["ai_sentiment"],
            lookback_days=14,
        )

    def compute(
        self,
        index_code: str,
        trade_date: date,
        ctx: FactorContext,
    ) -> FactorValue:
        """计算主题动量子。

        将最近 3 日关注度与再往前 3 日对比，计算变化率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: 因子上下文。

        Returns:
            FactorValue。
        """
        recent_attn: list[float] = []
        base_attn: list[float] = []

        # 当期：最近 3 天
        for delta in range(3):
            check_date = trade_date - timedelta(days=delta)
            agg = ctx.ai_sentiment.get((index_code, check_date))
            if agg is not None:
                recent_attn.append(agg.total_attention)

        # 基期：往前 3-6 天
        for delta in range(4, 7):
            check_date = trade_date - timedelta(days=delta)
            agg = ctx.ai_sentiment.get((index_code, check_date))
            if agg is not None:
                base_attn.append(agg.total_attention)

        if len(recent_attn) < 2 or len(base_attn) < 1:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={
                    "reason": f"当期 {len(recent_attn)}/3 天，基期 {len(base_attn)}/3 天，数据不足",
                },
            )

        recent_avg = sum(recent_attn) / len(recent_attn)
        base_avg = sum(base_attn) / len(base_attn)

        if base_avg < 0.5:
            # 基期关注度极低，新出现的话题视为高动量
            if recent_avg > 1:
                momentum = min(200.0, recent_avg * 100)
            else:
                momentum = 0.0
        else:
            momentum = ((recent_avg - base_avg) / base_avg) * 100

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(momentum, 2),
            payload={
                "recent_avg": round(recent_avg, 2),
                "base_avg": round(base_avg, 2),
                "recent_days": len(recent_attn),
                "base_days": len(base_attn),
            },
        )
