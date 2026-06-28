"""AI 因子计算器集合。

所有 AI 因子都遵循 quant_etf_api.factors.base.FactorComputer 协议，
可通过 FactorRegistry 注册并被策略引擎引用。
"""

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

__all__ = [
    "Sentiment1dComputer",
    "Sentiment5dComputer",
    "SentimentDivergenceComputer",
    "Attention1dComputer",
    "Attention5dComputer",
    "TopicMomentumComputer",
]
