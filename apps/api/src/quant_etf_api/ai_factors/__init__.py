"""AI 舆情分析层。

本包提供以下能力：
- 新闻采集与标准化：从 NewsNow API / RSS 采集新闻数据
- AI 情绪分析：调用 LLM 对新闻进行情绪评分和资产关联
- 热度/趋势评分：基于排名、频次的非 AI 热度量化
- 市场综合研判：基于当日情绪聚合数据生成市场概况

说明：AI 情绪分析功能当前阶段不完善，6 个 AI 因子
（ai_sentiment_1d/5d/divergence、ai_attention_1d/5d、ai_topic_momentum）
已从策略引擎中移除，本包仅保留舆情分析与展示能力，不再向策略引擎提供因子。

使用示例::

    from quant_etf_api.ai_factors.data.collector import NewsCollector
    from quant_etf_api.ai_factors.analysis.sentiment import SentimentAnalyzer
    from quant_etf_api.infra.ai import AIClient
    from quant_etf_api.config.settings import get_settings

    settings = get_settings()
    client = AIClient.from_settings(settings)
    analyzer = SentimentAnalyzer(client)
"""
