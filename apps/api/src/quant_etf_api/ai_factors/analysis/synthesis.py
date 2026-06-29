"""市场综合研判生成器。

从 TrendRadar 项目的深度分析能力抽象而来：基于当日的 AI 情绪聚合数据，
调用 LLM 生成一份 200-300 字的中文市场概况，识别关键主题和风险信号。

使用示例::

    from quant_etf_api.ai_factors.analysis.synthesis import MarketSynthesisAnalyzer
    from quant_etf_api.infra.ai.client import AIClient

    analyzer = MarketSynthesisAnalyzer(client)
    result = analyzer.generate(aggregates, trade_date)
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from quant_etf_api.ai_factors.base import DailySentimentAggregate
from quant_etf_api.infra.ai.client import AIClient
from quant_etf_api.infra.ai.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

PROMPT_NAME = "market_synthesis"


class MarketSynthesisAnalyzer:
    """市场综合研判生成器。

    调用 LLM 综合当日情绪聚合数据，生成中文市场概况。
    LLM 不可用或响应解析失败时返回 None，不阻断主链路。
    """

    def __init__(self, client: AIClient) -> None:
        """初始化研判生成器。

        Args:
            client: AI 客户端实例。
        """
        self._client = client
        self._prompt_loader = PromptLoader()

    def generate(
        self,
        aggregates: list[DailySentimentAggregate],
        target_date: date,
    ) -> dict | None:
        """生成当日市场综合研判。

        Args:
            aggregates: 当日情绪聚合数据列表（来自 TrendScorer.aggregate_daily）。
            target_date: 目标交易日。

        Returns:
            包含 content / key_topics / risk_notes / sentiment_summary 的字典，
            LLM 不可用或解析失败时返回 None。
        """
        if not aggregates:
            logger.warning("无聚合数据，跳过市场研判生成")
            return None

        if not self._client.api_key:
            logger.warning("未配置 LLM API Key，跳过市场研判生成")
            return None

        # 构建情绪摘要 JSON（供 LLM 参考）
        sentiment_summary: dict[str, dict] = {}
        for agg in aggregates:
            sentiment_summary[agg.asset_tag] = {
                "avg_sentiment": round(agg.avg_sentiment, 4),
                "weighted_sentiment": round(agg.weighted_sentiment, 4),
                "total_attention": round(agg.total_attention, 2),
                "news_count": agg.news_count,
                "positive_ratio": round(agg.positive_ratio, 4),
                "negative_ratio": round(agg.negative_ratio, 4),
                "top_topics": agg.top_topics[:5] if agg.top_topics else [],
            }

        sentiment_json = json.dumps(sentiment_summary, ensure_ascii=False, indent=2)

        # 构建 prompt
        system_prompt, user_prompt = self._prompt_loader.render(
            PROMPT_NAME,
            variables={
                "current_date": target_date.isoformat(),
                "sentiment_data": sentiment_json,
            },
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            data = self._client.chat_json_with_repair(messages, max_tokens=1024)
            if data is None:
                logger.warning("市场研判 JSON 解析失败（含 AI 修复）")
                return None

            content = str(data.get("content", "")).strip()
            if not content:
                logger.warning("市场研判 content 为空")
                return None

            key_topics = data.get("key_topics", [])
            if isinstance(key_topics, str):
                key_topics = [t.strip() for t in key_topics.split(",") if t.strip()]
            if not isinstance(key_topics, list):
                key_topics = []

            risk_notes = str(data.get("risk_notes", "")).strip() or None

            return {
                "content": content,
                "key_topics": key_topics[:8],
                "risk_notes": risk_notes,
                "sentiment_summary": sentiment_summary,
            }
        except Exception:
            logger.exception("市场研判 LLM 调用失败")
            return None

    @staticmethod
    def to_db_row(
        result: dict,
        target_date: date,
        llm_model: str,
    ) -> dict:
        """将 generate() 的结果转换为 DB 写入行。

        Args:
            result: generate() 的返回值（非 None）。
            target_date: 交易日。
            llm_model: LLM 模型标识。

        Returns:
            可直接传给 MarketSynthesisRepository.save() 的字典。
        """
        import uuid

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return {
            "id": str(uuid.uuid4()),
            "trade_date": target_date,
            "content": result["content"],
            "sentiment_summary": result["sentiment_summary"],
            "key_topics": result["key_topics"],
            "risk_notes": result.get("risk_notes"),
            "llm_model": llm_model,
            "created_at": now,
        }
