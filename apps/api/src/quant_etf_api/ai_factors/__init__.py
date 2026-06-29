"""AI 舆情 / 新闻 / 热点因子层。

本包提供以下能力：
- 新闻采集与标准化：从 NewsNow API / RSS 采集新闻数据
- AI 情绪分析：调用 LLM 对新闻进行情绪评分和资产关联
- 热度/趋势评分：基于排名、频次的非 AI 热度量化
- 市场综合研判：基于当日情绪聚合数据生成市场概况
- AI 因子计算：遵循 FactorComputer 协议，可被策略引擎引用

---- AI 因子在策略引擎中的使用注意事项 ----

1. **数据覆盖**: AI 因子依赖每日 AI 分析（``POST /ai-factors/analyze`` 或自动调度），
   仅在已有情绪数据的交易日有效。未执行 AI 分析的日期，所有 AI 因子返回 ``None``。

2. **评分配置建议**: 对 AI 因子使用 ``missing_factor_strategy: "ignore"``（默认值），
   这样 AI 数据缺失时该因子被静默跳过，不影响其他因子的评分计算。
   不要使用 ``"exclude"`` 策略 — 这会直接移除资产。

3. **过滤器建议**: 不要在 filter 规则中使用 AI 因子。
   过滤器对 None 值一律视为"规则失败" → 资产被过滤。
   如果 AI 数据某天缺失，所有资产都会因 filter 失败而变成空仓。

4. **回测场景**: 回测期间 AI 因子仅在已有历史 AI 情绪数据的日期生效。
   如果从未对历史日期运行过 AI 分析，回测中的 AI 因子将全程返回 None
   （被评分引擎的 ``ignore`` 策略跳过）。

5. **transform 函数**: AI 因子值域与传统因子不同（情绪 [-1,1]、关注度 [0,~200]），
   建议使用专用 transform 函数：
   - ``sentiment_score``: 情绪分 [-1,1] → 得分 [0,100]
   - ``attention_score``: 关注度 → 得分 [0,100]（裁剪）

使用示例::

    from quant_etf_api.ai_factors.data.collector import NewsCollector
    from quant_etf_api.ai_factors.analysis.sentiment import SentimentAnalyzer
    from quant_etf_api.infra.ai import AIClient
    from quant_etf_api.config.settings import get_settings

    settings = get_settings()
    client = AIClient.from_settings(settings)
    analyzer = SentimentAnalyzer(client)
"""
