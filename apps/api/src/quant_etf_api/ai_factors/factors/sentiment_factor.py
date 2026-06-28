"""AI 情绪因子计算器。

遵循 FactorComputer 协议，从 FactorContext.ai_sentiment 读取 AI 情绪聚合数据，
计算情绪相关因子值。

因子包括：
- ai_sentiment_1d: 最近 1 个交易日加权情绪分
- ai_sentiment_5d: 最近 5 个交易日情绪移动平均
- ai_sentiment_divergence: 情绪分歧度（多源情绪标准差）
"""

from __future__ import annotations

from datetime import date, timedelta

from quant_etf_api.ai_factors.base import DailySentimentAggregate
from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


class Sentiment1dComputer:
    """1 日 AI 情绪因子计算器。

    从 ai_sentiment 上下文中读取对应指数的加权情绪分，
    数据不足时返回 None（不做填充）。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 1 日情绪因子的元数据。"""
        return FactorSpec(
            factor_id="ai_sentiment_1d",
            name="AI情绪因子(1日)",
            category="sentiment",
            version="1.0.0",
            description="基于大模型 NLP 分析的指数相关新闻加权情绪得分（1 日），正=利好，负=利空。",
            required_data=["ai_sentiment"],
            lookback_days=7,
        )

    def compute(
        self,
        index_code: str,
        trade_date: date,
        ctx: FactorContext,
    ) -> FactorValue:
        """计算 1 日情绪因子的值。

        Args:
            index_code: 指数代码（如 000300）。
            trade_date: 目标交易日。
            ctx: 包含 ai_sentiment 字段的因子上下文。

        Returns:
            FactorValue，无 AI 数据时 numeric=None。
        """
        agg = ctx.ai_sentiment.get((index_code, trade_date))
        if agg is None:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": f"{index_code} 在 {trade_date} 无 AI 情绪数据"},
            )

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(agg.weighted_sentiment, 4),
            payload={
                "news_count": agg.news_count,
                "avg_sentiment": agg.avg_sentiment,
                "total_attention": agg.total_attention,
            },
        )


class Sentiment5dComputer:
    """5 日 AI 情绪因子计算器。

    计算最近 5 个交易日的情绪移动平均，平滑单日噪声。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 5 日情绪因子的元数据。"""
        return FactorSpec(
            factor_id="ai_sentiment_5d",
            name="AI情绪因子(5日均值)",
            category="sentiment",
            version="1.0.0",
            description="指数相关新闻 AI 情绪的 5 日移动平均，平滑单日噪声，捕捉中期情绪趋势。",
            required_data=["ai_sentiment"],
            lookback_days=14,
        )

    def compute(
        self,
        index_code: str,
        trade_date: date,
        ctx: FactorContext,
    ) -> FactorValue:
        """计算 5 日情绪均值的因子值。

        取 trade_date 及前 4 个自然日内的可用数据做简单平均。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: 包含 ai_sentiment 字段的因子上下文。

        Returns:
            FactorValue，至少需要 3 天数据，不足时返回 None。
        """
        values: list[float] = []
        for delta in range(5):
            check_date = trade_date - timedelta(days=delta)
            agg = ctx.ai_sentiment.get((index_code, check_date))
            if agg is not None:
                values.append(agg.weighted_sentiment)

        if len(values) < 3:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": f"仅 {len(values)}/5 天有数据，需要至少 3 天"},
            )

        avg = sum(values) / len(values)
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(avg, 4),
            payload={"days": len(values), "values": [round(v, 4) for v in values]},
        )


class SentimentDivergenceComputer:
    """情绪分歧因子计算器。

    计算同一交易日内不同来源/标签的情绪标准差，
    反映市场舆论的分歧程度 — 分歧越大，不确定性越高。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回情绪分歧因子的元数据。"""
        return FactorSpec(
            factor_id="ai_sentiment_divergence",
            name="AI情绪分歧因子",
            category="sentiment",
            version="1.0.0",
            description="多源情绪的离散度（标准差），分歧越大表示市场不确定性越高，常为反向指标。",
            required_data=["ai_sentiment"],
            lookback_days=7,
        )

    def compute(
        self,
        index_code: str,
        trade_date: date,
        ctx: FactorContext,
    ) -> FactorValue:
        """计算情绪分歧因子。

        遍历 ctx.ai_sentiment 中 trade_date 的所有 asset_tag 的加权情绪，
        计算标准差作为分歧度。

        Args:
            index_code: 指数代码（用于筛选相关标签）。
            trade_date: 目标交易日。
            ctx: 包含 ai_sentiment 字段的因子上下文。

        Returns:
            FactorValue，numeric 为情绪标准差（越大分歧越高）。
        """
        # 收集当日所有标签的情绪值
        sentiments: list[float] = []
        for (tag, dt), agg in ctx.ai_sentiment.items():
            if dt == trade_date and agg.news_count > 0:
                sentiments.append(agg.weighted_sentiment)

        n = len(sentiments)
        if n < 3:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": f"仅 {n} 个标签组有数据，需要至少 3 个"},
            )

        mean = sum(sentiments) / n
        variance = sum((s - mean) ** 2 for s in sentiments) / n
        std_dev = variance ** 0.5

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(std_dev, 4),
            payload={
                "n_groups": n,
                "mean": round(mean, 4),
                "min": round(min(sentiments), 4),
                "max": round(max(sentiments), 4),
            },
        )
