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

import logging
import re
from datetime import datetime, timezone

from quant_etf_api.ai_factors.base import NewsSentimentItem, RawNewsItem
from quant_etf_api.ai_factors.analysis.scorer import TrendScorer
from quant_etf_api.infra.ai.client import AIClient
from quant_etf_api.infra.ai.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

# 默认 prompt 模板名称
PROMPT_NAME = "sentiment_analysis"

# 批量分析时每次发送给 AI 的最大新闻条数
# 每条分析约需 100-150 tokens 输出，默认 max_tokens=2000，保守设为 10 条/批
DEFAULT_BATCH_SIZE = 10

# 已知财经来源 ID（这些来源的新闻直接视为金融相关，跳过关键词过滤）
_FINANCE_SOURCE_IDS: frozenset[str] = frozenset(
    [
        "wallstreetcn-hot",  # 华尔街见闻
        "cls-hot",  # 财联社热门
        "thepaper",  # 澎湃新闻（含财经频道）
    ]
)
# 已知财经 RSS 源域名前缀
_FINANCE_RSS_DOMAINS: tuple[str, ...] = (
    "https://finance.yahoo.com",
    "https://feeds.content.dowjones.io",
    "https://www.economist.com",
)


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
        available_tags: list[str] | None = None,
    ) -> list[NewsSentimentItem]:
        """批量分析新闻情绪。

        将新闻分批发送给 LLM，每批最多 batch_size 条。

        Args:
            items: 原始新闻列表。
            available_tags: 可用标签列表，默认使用 ALL_AVAILABLE_TAGS。

        Returns:
            NewsSentimentItem 列表，保持输入顺序。AI 调用失败时返回
            仅含注意力分数的结果（sentiment_score=0）。
        """
        if not items:
            return []

        if available_tags is None:
            from quant_etf_api.ai_factors.base import ALL_AVAILABLE_TAGS

            available_tags = ALL_AVAILABLE_TAGS

        # 过滤：仅保留可能与金融相关的新闻
        # 判定逻辑：来源为财经平台 OR 标题含金融关键词
        from quant_etf_api.ai_factors.data.cleaner import TextCleaner

        finance_items: list[RawNewsItem] = []
        non_finance: list[RawNewsItem] = []
        for it in items:
            is_finance_source = it.source_id in _FINANCE_SOURCE_IDS or it.source_id.startswith(
                _FINANCE_RSS_DOMAINS
            )
            if is_finance_source or TextCleaner.is_finance_related(it.title):
                finance_items.append(it)
            else:
                non_finance.append(it)

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
            batch_results = self._analyze_single_batch(batch, available_tags)
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
        available_tags: list[str],
    ) -> list[NewsSentimentItem]:
        """调用 LLM 分析一批新闻。

        使用 chat_json_with_repair：LiteLLM 自动处理 DeepSeek 的 reasoning_content，
        JSON 解析失败时自动请求 AI 修复，两次尝试都失败才回退。
        """
        if not self._client.api_key:
            logger.warning("未配置 LLM API Key，跳过 AI 情绪分析")
            return self._fallback_results(items)

        # 构建 prompt
        system_prompt, user_prompt = self._prompt_loader.render(
            PROMPT_NAME,
            variables={
                "current_date": datetime.now().strftime("%Y-%m-%d"),
                "available_tags": ", ".join(available_tags),
                "news_list": self._format_news(items),
            },
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            # 先尝试 chat_json_with_repair（LiteLLM + json_repair + AI修复）
            data = self._client.chat_json_with_repair(
                messages,
                max_tokens=self._client.max_tokens,
            )
            if data is not None:
                result = self._build_from_json(data, items)
                if result:
                    return result
        except Exception:
            logger.exception("AI 情绪分析调用失败")

        # JSON 解析失败 → 尝试直接 chat + reasoning 文本解析
        try:
            raw = self._client.chat(messages, max_tokens=self._client.max_tokens)
            if raw and raw.strip():
                parsed = self._parse_reasoning_text(raw, items)
                if parsed:
                    return parsed
        except Exception:
            pass

        # 所有方式都失败时返回降级结果
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

    # DeepSeek 推理文本中条目分隔符的模式（支持多种变体）
    _ENTRY_DELIMITER: re.Pattern = re.compile(
        r"\n(?="
        r"\[\d+\]"  # [0]
        r"|"
        r"\d+\.\s"  # 1.
        r"|"
        r"\d+\."
        "            # 1."
        r")"
    )
    # 条目开头的索引提取（支持 [0]、0.、0." 三种格式）
    _ENTRY_INDEX: re.Pattern = re.compile(r"^\[(\d+)\]|^(\d+)[\.．]")

    def _parse_reasoning_text(
        self,
        raw: str,
        items: list[RawNewsItem],
    ) -> list[NewsSentimentItem] | None:
        """解析 DeepSeek reasoning_content 中的结构化自然语言文本。

        DeepSeek 的输出格式不固定，常见变体：
            [0] 标题：xxx ...
              sentiment_score: 0.3, relevance_score: 0.5
              asset_tags: 科技, AI
              topics: ["芯片", "算力"]
              summary: 摘要文本

            1. "新闻标题" - 分析...
              sentiment_score: 0.3, relevance_score: 0.5
              ...

        Args:
            raw: reasoning_content 原始文本。
            items: 原始新闻列表。

        Returns:
            解析后的 NewsSentimentItem 列表，格式不匹配返回 None。
        """
        # 按条目分隔符切割
        parts = self._ENTRY_DELIMITER.split(raw)
        if not parts:
            return None

        # 第一部分可能是前言（非条目文本），跳过
        entries = [p for p in parts if self._ENTRY_INDEX.search(p)]
        if not entries:
            return None

        now = datetime.now(timezone.utc)
        results: list[NewsSentimentItem] = []

        for entry_text in entries:
            entry_text = entry_text.strip()

            # 提取索引
            idx_match = self._ENTRY_INDEX.search(entry_text)
            if not idx_match:
                continue
            idx = int(idx_match.group(1) or idx_match.group(2))
            if not (0 <= idx < len(items)):
                continue

            item = items[idx]

            # 提取各字段（中英文冒号均支持）
            sentiment = self._extract_float(entry_text, r"sentiment_score[：:]\s*(-?[\d.]+)")
            relevance = self._extract_float(entry_text, r"relevance_score[：:]\s*(-?[\d.]+)")

            # 提取 asset_tags（支持纯文本和 JSON 数组两种格式）
            tags = self._extract_tags_field(entry_text, "asset_tags")

            # 提取 topics（支持纯文本和 JSON 数组两种格式）
            topics = self._extract_tags_field(entry_text, "topics")

            # 提取 summary
            summary = ""
            sm = re.search(
                r'summary[：:]\s*[\""]?(.+?)[\""]?(?:\n|\n\[|\n\d+[\.．]|$)', entry_text, re.DOTALL
            )
            if sm:
                summary = sm.group(1).strip()[:100]

            # 计算关注度
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
                    asset_tags=tags[:5],
                    sentiment_score=round(sentiment, 4),
                    attention_score=round(attention, 2),
                    relevance_score=round(relevance, 4),
                    topics=topics[:5],
                    summary=summary,
                    raw_text=item.title,
                )
            )

        # 补齐未匹配的 items
        while len(results) < len(items):
            missing = items[len(results)]
            results.append(
                NewsSentimentItem(
                    timestamp=now,
                    source=missing.source_id,
                    source_name=missing.source_name,
                    title=missing.title,
                    url=missing.url,
                    attention_score=self._scorer.calculate_attention_score(
                        rank=missing.ranks[0] if missing.ranks else 99,
                        count=missing.appear_count,
                    ),
                    raw_text=missing.title,
                )
            )

        nonzero = sum(1 for r in results if r.sentiment_score != 0)
        logger.info("从 reasoning 文本解析出 %d/%d 条有效分析结果", nonzero, len(results))
        return results

    @staticmethod
    def _extract_tags_field(text: str, field_name: str) -> list[str]:
        """从文本中提取标签字段（支持纯文本和 JSON 数组两种格式）。

        格式变体：
            asset_tags: 科技, AI
            asset_tags: ["科技", "AI"]
            asset_tags: [] 或者宏观
            asset_tags: 无特别标签

        Args:
            text: 条目文本。
            field_name: 字段名（如 "asset_tags"、"topics"）。

        Returns:
            清洗后的标签列表。
        """
        # 匹配字段值：从 key: 开始到行尾（或遇到换行+下一字段/新条目）
        pattern = (
            rf"{field_name}[：:]"  # key:
            r"\s*"  # 可选空格
            r"(.+?)"  # 值（非贪婪）
            r"(?:\n(?:sentiment_|relevance_|topics|asset_tags|summary)|\n\s*\n|\Z)"  # 到下一字段或结束
        )
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            return []

        raw_val = m.group(1).strip()

        # JSON 数组格式：["tag1", "tag2"]
        if raw_val.startswith("["):
            import json

            try:
                bracket_end = raw_val.find("]")
                if bracket_end != -1:
                    arr = json.loads(raw_val[: bracket_end + 1])
                    if isinstance(arr, list):
                        return [str(t) for t in arr if t][:5]
            except (json.JSONDecodeError, TypeError):
                pass

        # 纯文本格式：tag1, tag2 或 tag1；tag2 等
        # 先清理无意义的描述
        cleaned = re.sub(r"[无没]有?特[别殊]标签[。，]*", "", raw_val)
        cleaned = re.sub(r"可以不加[。，]*", "", cleaned)
        cleaned = re.sub(r"或者\S+", "", cleaned)
        tags = [t.strip() for t in re.split(r"[，,、；;]", cleaned) if t.strip()]
        return [t for t in tags if t and "可能" not in t and "?" not in t][:5]

    @staticmethod
    def _extract_float(text: str, pattern: str) -> float:
        """从文本中提取浮点数，失败返回 0.0。"""
        m = re.search(pattern, text)
        if m:
            try:
                val = float(m.group(1))
                return max(-1.0, min(1.0, val))
            except (TypeError, ValueError):
                pass
        return 0.0

    def _build_from_json(
        self,
        data: dict | list,
        items: list[RawNewsItem],
    ) -> list[NewsSentimentItem] | None:
        """从解析后的 JSON dict/list 构建 NewsSentimentItem 列表。

        LiteLLM 已处理好 reasoning_content → 此处只需从标准 JSON 中提取字段。

        Args:
            data: chat_json_with_repair 返回的已解析 JSON（dict 或 list）。
            items: 原始新闻列表。

        Returns:
            NewsSentimentItem 列表，无法提取 entries 时返回 None。
        """
        entries: list[dict] = []
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = (
                data.get("results", [])
                or data.get("data", [])
                or data.get("items", [])
                or data.get("analyses", [])
                or []
            )
            if not entries and ("sentiment_score" in data or "index" in data):
                entries = [data]

        if not entries:
            logger.warning(
                "JSON 已解析但无有效 entries，data keys=%s",
                list(data.keys())[:5] if isinstance(data, dict) else "list",
            )
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
