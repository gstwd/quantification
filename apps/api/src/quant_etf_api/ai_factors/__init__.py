"""AI 舆情 / 新闻 / 热点因子层。

本包提供以下能力：
- 新闻采集与标准化：从 NewsNow API / RSS 采集新闻数据
- AI 情绪分析：调用 LLM 对新闻进行情绪评分和资产关联
- 热度/趋势评分：基于排名、频次的非 AI 热度量化
- AI 因子计算：遵循 FactorComputer 协议，可被策略引擎引用

使用示例::

    from quant_etf_api.ai_factors.data.collector import NewsCollector
    from quant_etf_api.ai_factors.analysis.sentiment import SentimentAnalyzer
    from quant_etf_api.infra.ai import AIClient
    from quant_etf_api.config.settings import get_settings

    settings = get_settings()
    client = AIClient.from_settings(settings)
    analyzer = SentimentAnalyzer(client)
"""
