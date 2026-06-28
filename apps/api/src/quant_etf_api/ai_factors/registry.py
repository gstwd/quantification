"""AI 因子注册表。

将 AI 因子计算器注册到项目的全局 FactorRegistry 中，
使 AI 因子可以像内置因子一样被策略引擎引用。
"""

from __future__ import annotations

from quant_etf_api.ai_factors.factors.attention_factor import (
    Attention1dComputer,
    Attention5dComputer,
)
from quant_etf_api.ai_factors.factors.sentiment_factor import (
    Sentiment1dComputer,
    Sentiment5dComputer,
    SentimentDivergenceComputer,
)
from quant_etf_api.ai_factors.factors.topic_momentum_factor import TopicMomentumComputer
from quant_etf_api.factors.registry import FactorRegistry


def register_ai_factors(registry: FactorRegistry) -> None:
    """将所有 AI 因子计算器注册到给定的 FactorRegistry 中。

    应在 build_default_factor_registry() 中调用此函数，
    确保 AI 因子随内置因子一起注册。

    Args:
        registry: 目标 FactorRegistry 实例。
    """
    # 情绪因子
    registry.register(Sentiment1dComputer())
    registry.register(Sentiment5dComputer())
    registry.register(SentimentDivergenceComputer())

    # 关注度因子
    registry.register(Attention1dComputer())
    registry.register(Attention5dComputer())

    # 主题动量
    registry.register(TopicMomentumComputer())
