"""AI 情绪分析器。

从 TrendRadar 项目的 ai/analyzer.py 提取核心分析逻辑，
重写为独立、可测试的服务。调用 LLM 对新闻进行情绪评分。

使用示例::

    from quant_etf_api.infra.ai import AIClient
    from quant_etf_api.ai_factors.analysis import SentimentAnalyzer
    from quant_etf_api.config.settings import get_settings

    settings = get_settings()
    client = AIClient.from_settings(settings)
    analyzer = SentimentAnalyzer(client)
    results = analyzer.analyze_batch(news_items)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from quant_etf_api.ai_factors.base import ALL_AVAILABLE_TAGS, NewsSentimentItem, RawNewsItem
from quant_etf_api.ai_factors.analysis.scorer import TrendScorer
from quant_etf_api.infra.ai.client import AIClient
from quant_etf_api.infra.ai.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

# 默认 prompt 模板名称
PROMPT_NAME = "sentiment_analysis"

# 批量分析时每次发送给 AI 的最大新闻条数
DEFAULT_BATCH_SIZE = 50

# 默认市场背景（无特殊背景时）
DEFAULT_MARKET_CONTEXT = "正常交易时段，无特殊宏观事件"


class SentimentAnalyzer:
    """AI 情绪分析器。

    调用 LLM 对新闻标题进行情绪评分、资产关联和主题提取。
    内部使用 TrendScorer 计算非 AI 的关注度分数。
    """

    def __init__(
        self,
        client: AIClient,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        """初始化情绪分析器。

        Args:
            client: AI 客户端实例。
            batch_size: 每批次发送给 LLM 的最大新闻数。
        """
        self._client = client
        self._batch_size = batch_size
        self._prompt_loader = PromptLoader()
        self._scorer = TrendScorer()

    def analyze_batch(
        self,
        items: list[RawNewsItem],
        market_context: str = DEFAULT_MARKET_CONTEXT,
    ) -> list[NewsSentimentItem]:
        """批量分析新闻情绪。

        将新闻分批发送给 LLM，每批最多 batch_size 条。

        Args:
            items: 原始新闻列表。
            market_context: 当前市场背景（如 "央行降准后次日"）。

        Returns:
            NewsSentimentItem 列表，保持输入顺序。AI 调用失败时返回
            仅含注意力分数的结果（sentiment_score=0）。
        """
        if not items:
            return []

        # 过滤：仅保留可能与金融相关的新闻
        from quant_etf_api.ai_factors.data.cleaner import TextCleaner

        finance_items = [it for it in items if TextCleaner.is_finance_related(it.title)]
        non_finance = [it for it in items if not TextCleaner.is_finance_related(it.title)]

        logger.info(
            "新闻过滤: %d 条中 %d 条金融相关, %d 条跳过",
            len(items),
            len(finance_items),
            len(non_finance),
        )

        # 非金融新闻也保留，但 sentiment/relevance 为 0，attention 正常计算
        results: list[NewsSentimentItem] = []

        # 处理金融相关新闻（分批 LLM 分析）
        for i in range(0, len(finance_items), self._batch_size):
            batch = finance_items[i : i + self._batch_size]
            batch_results = self._analyze_single_batch(batch, market_context)
            results.extend(batch_results)

        # 处理非金融新闻（跳过 AI 分析）
        now = datetime.now(timezone.utc)
        for item in non_finance:
            results.append(
                NewsSentimentItem(
                    timestamp=now,
                    source=item.source_id,
                    source_name=item.source_name,
                    title=item.title,
                    url=item.url,
                    sentiment_score=0.0,
                    relevance_score=0.0,
                    attention_score=self._scorer.calculate_attention_score(
                        rank=item.ranks[0] if item.ranks else 99,
                        count=item.appear_count,
                    ),
                    raw_text=item.title,
                )
            )

        return results

    def _analyze_single_batch(
        self,
        items: list[RawNewsItem],
        market_context: str,
    ) -> list[NewsSentimentItem]:
        """调用 LLM 分析一批新闻。"""
        if not self._client.api_key:
            logger.warning("未配置 LLM API Key，跳过 AI 情绪分析")
            return self._fallback_results(items)

        # 构建 prompt
        system_prompt, user_prompt = self._prompt_loader.render(
            PROMPT_NAME,
            variables={
                "current_date": datetime.now().strftime("%Y-%m-%d"),
                "market_context": market_context,
                "available_tags": ", ".join(ALL_AVAILABLE_TAGS),
                "news_list": self._format_news(items),
            },
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            raw_response = self._client.chat(messages, max_tokens=self._client.max_tokens)
            parsed = self._parse_response(raw_response, items)
            if parsed:
                return parsed
        except Exception:
            logger.exception("AI 情绪分析调用失败")

        # AI 失败时返回降级结果
        return self._fallback_results(items)

    def _format_news(self, items: list[RawNewsItem]) -> str:
        """将新闻列表格式化为 prompt 文本。

        Args:
            items: 原始新闻列表。

        Returns:
            格式化的文本，包含索引、来源、标题等信息。
        """
        lines: list[str] = []
        for idx, item in enumerate(items):
            rank_str = f"排名{min(item.ranks)}" if item.ranks else "无排名"
            lines.append(
                f"[{idx}] [{item.source_name}] {item.title} | {rank_str}"
                f" | 出现{item.appear_count}次"
            )
        return "\n".join(lines)

    def _parse_response(
        self,
        raw: str,
        items: list[RawNewsItem],
    ) -> list[NewsSentimentItem] | None:
        """解析 LLM JSON 响应为 NewsSentimentItem 列表。

        Args:
            raw: LLM 原始响应。
            items: 原始新闻列表（用于回填标题等字段）。

        Returns:
            解析后的列表，失败返回 None。
        """
        from quant_etf_api.infra.ai.client import _extract_json

        data = _extract_json(raw)

        # 处理两种情况：数组格式 或 {"results": [...]} 格式
        entries: list[dict] = []
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = data.get("results", []) or data.get("data", []) or []

        if not entries:
            logger.warning("LLM 返回空结果")
            return None

        now = datetime.now(timezone.utc)
        results: list[NewsSentimentItem] = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            # 索引对齐：entry 中的 index 映射到 items 列表
            idx = entry.get("index", len(results))
            if 0 <= idx < len(items):
                item = items[idx]
            else:
                item = items[len(results)] if len(results) < len(items) else None

            if item is None:
                continue

            # 解析情绪分，确保在 [-1, 1] 范围内
            raw_score = entry.get("sentiment_score", 0)
            try:
                sentiment = float(raw_score)
                sentiment = max(-1.0, min(1.0, sentiment))
            except (TypeError, ValueError):
                sentiment = 0.0

            # 解析相关度
            raw_rel = entry.get("relevance_score", 0)
            try:
                relevance = float(raw_rel)
                relevance = max(0.0, min(1.0, relevance))
            except (TypeError, ValueError):
                relevance = 0.0

            # 解析标签
            tags = entry.get("asset_tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]
            if not isinstance(tags, list):
                tags = []

            topics = entry.get("topics", [])
            if isinstance(topics, str):
                topics = [t.strip() for t in topics.split(",")]
            if not isinstance(topics, list):
                topics = []

            # 计算关注度（非 AI，基于排名）
            attention = self._scorer.calculate_attention_score(
                rank=item.ranks[0] if item.ranks else 99,
                count=item.appear_count,
            )

            results.append(
                NewsSentimentItem(
                    timestamp=now,
                    source=item.source_id,
                    source_name=item.source_name,
                    title=item.title,
                    url=item.url,
                    asset_tags=tags[:5],  # 最多5个标签
                    sentiment_score=round(sentiment, 4),
                    attention_score=round(attention, 2),
                    relevance_score=round(relevance, 4),
                    topics=topics[:5],
                    summary=str(entry.get("summary", ""))[:100],
                    raw_text=item.title,
                )
            )

        # 补齐未匹配的 items
        while len(results) < len(items):
            missing_item = items[len(results)]
            results.append(
                NewsSentimentItem(
                    timestamp=now,
                    source=missing_item.source_id,
                    source_name=missing_item.source_name,
                    title=missing_item.title,
                    url=missing_item.url,
                    attention_score=self._scorer.calculate_attention_score(
                        rank=missing_item.ranks[0] if missing_item.ranks else 99,
                        count=missing_item.appear_count,
                    ),
                    raw_text=missing_item.title,
                )
            )

        return results

    def _fallback_results(self, items: list[RawNewsItem]) -> list[NewsSentimentItem]:
        """AI 不可用时的降级结果：仅计算关注度分数。

        Args:
            items: 原始新闻列表。

        Returns:
            sentiment=0 但 attention 正常的结果列表。
        """
        now = datetime.now(timezone.utc)
        return [
            NewsSentimentItem(
                timestamp=now,
                source=item.source_id,
                source_name=item.source_name,
                title=item.title,
                url=item.url,
                attention_score=self._scorer.calculate_attention_score(
                    rank=item.ranks[0] if item.ranks else 99,
                    count=item.appear_count,
                ),
                raw_text=item.title,
            )
            for item in items
        ]
