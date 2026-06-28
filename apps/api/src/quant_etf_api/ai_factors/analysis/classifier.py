"""AI 标签分类器。

从 TrendRadar 项目的 ai/filter.py 提取批量分类逻辑，
将新闻自动分类到预定义的资产/行业标签。

使用示例::

    classifier = TagClassifier(client)
    classified = classifier.classify_to_asset_tags(sentiment_items)
"""

from __future__ import annotations

import logging
from typing import ClassVar

from quant_etf_api.ai_factors.base import ALL_AVAILABLE_TAGS, NewsSentimentItem
from quant_etf_api.infra.ai.client import AIClient
from quant_etf_api.infra.ai.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

PROMPT_NAME = "news_classify"

# 资产标签分类的 LLM 批次大小
DEFAULT_CLASSIFY_BATCH_SIZE = 100


class TagClassifier:
    """AI 标签分类器。

    将新闻标题分类到预定义的资产/行业/概念标签中。
    当 LLM 不可用时，使用关键词匹配作为降级方案。
    """

    # 内置关键词→标签映射（降级方案）
    _KEYWORD_TAG_MAP: ClassVar[dict[str, str]] = {
        # 指数
        "沪深300": "000300",
        "沪深 300": "000300",
        "中证500": "000905",
        "中证 500": "000905",
        "上证50": "000016",
        "上证 50": "000016",
        "创业板": "399006",
        "科创板": "000688",
        "科创50": "000688",
        "科创 50": "000688",
        "中证1000": "000852",
        "中证 1000": "000852",
        # 行业
        "银行": "金融",
        "证券": "金融",
        "保险": "金融",
        "基金": "金融",
        "AI": "人工智能",
        "大模型": "人工智能",
        "GPT": "人工智能",
        "芯片": "半导体",
        "集成电路": "半导体",
        "光刻": "半导体",
        "新能源车": "新能源",
        "电动车": "新能源",
        "光伏": "新能源",
        "储能": "新能源",
        "锂电池": "新能源",
        "锂电": "新能源",
        "固态电池": "新能源",
        "医药": "医药",
        "创新药": "医药",
        "医疗器械": "医药",
        "房地产": "地产",
        "楼市": "地产",
        "军工": "军工",
        "国防": "军工",
        "消费": "消费",
        "零售": "消费",
        "电商": "消费",
        "机器人": "机器人",
        "人形机器人": "人形机器人",
        "自动驾驶": "自动驾驶",
        "智能驾驶": "自动驾驶",
        "数字经济": "数字经济",
        "数据要素": "数字经济",
        "央企": "央企改革",
        "国企改革": "央企改革",
        "低空经济": "低空经济",
        "eVTOL": "低空经济",
    }

    def __init__(
        self,
        client: AIClient,
        batch_size: int = DEFAULT_CLASSIFY_BATCH_SIZE,
    ) -> None:
        """初始化标签分类器。

        Args:
            client: AI 客户端实例。
            batch_size: 每批发送给 LLM 的最大新闻数。
        """
        self._client = client
        self._batch_size = batch_size
        self._prompt_loader = PromptLoader()

    def classify_to_asset_tags(
        self,
        items: list[NewsSentimentItem],
        available_tags: list[str] | None = None,
    ) -> list[NewsSentimentItem]:
        """为每条新闻打上资产标签。

        优先使用 LLM 分类，失败时回退到关键词匹配。

        Args:
            items: 情绪分析结果列表（不含资产标签）。
            available_tags: 可用标签列表，默认使用 ALL_AVAILABLE_TAGS。

        Returns:
            填充了 asset_tags 字段的 NewsSentimentItem 列表。
        """
        if not items:
            return items

        if available_tags is None:
            available_tags = ALL_AVAILABLE_TAGS

        filter_items = [it for it in items if it.relevance_score > 0.2]
        no_relevance = [it for it in items if it.relevance_score <= 0.2]

        # 对无相关度的新闻，直接用关键词匹配
        for item in no_relevance:
            item.asset_tags = []

        if not filter_items:
            return items

        # 尝试 LLM 分类
        try:
            if self._client.api_key:
                self._classify_via_llm(filter_items, available_tags)
            else:
                logger.info("无 LLM API Key，使用关键词匹配分类")
                self._classify_via_keyword(filter_items)
        except Exception:
            logger.warning("LLM 分类失败，回退到关键词匹配", exc_info=True)
            self._classify_via_keyword(filter_items)

        return items

    def _classify_via_llm(
        self,
        items: list[NewsSentimentItem],
        available_tags: list[str],
    ) -> None:
        """使用 LLM 进行批量标签分类。"""
        tag_list = ", ".join(available_tags)

        for i in range(0, len(items), self._batch_size):
            batch = items[i : i + self._batch_size]
            news_text = "\n".join(f"[{j}] {item.title}" for j, item in enumerate(batch, start=i))

            system, user = self._prompt_loader.render(
                PROMPT_NAME,
                variables={
                    "available_tags": tag_list,
                    "news_list": news_text,
                },
            )

            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]

            try:
                raw = self._client.chat(
                    messages,
                    temperature=0.1,  # 分类任务用低温度
                )
                parsed = self._parse_classify_response(raw, batch)
                if parsed is not None:
                    continue  # 成功，继续下一批
            except Exception:
                logger.warning("LLM 分类批次 %d 失败", i // self._batch_size + 1)

            # LLM 失败，回退到关键词匹配
            self._classify_via_keyword(batch)

    def _parse_classify_response(
        self,
        raw: str,
        batch: list[NewsSentimentItem],
    ) -> list[NewsSentimentItem] | None:
        """解析 LLM 分类响应。"""
        from quant_etf_api.infra.ai.client import _extract_json

        data = _extract_json(raw)
        if not isinstance(data, list) and isinstance(data, dict):
            data = data.get("results", []) or data.get("classifications", []) or []

        if not isinstance(data, list) or not data:
            return None

        for entry in data:
            if not isinstance(entry, dict):
                continue

            idx = entry.get("index", -1)
            if not isinstance(idx, int):
                continue

            # 在 batch 中查找对应项
            batch_idx = idx % len(batch) if batch else -1
            if batch_idx < 0 or batch_idx >= len(batch):
                continue

            tags = entry.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]

            batch[batch_idx].asset_tags = tags[:5]

        return batch

    def _classify_via_keyword(self, items: list[NewsSentimentItem]) -> None:
        """使用关键词匹配进行降级分类。

        Args:
            items: 待分类的新闻列表（原地修改）。
        """
        for item in items:
            text = item.title + " " + (item.summary or "")
            matched_tags: list[str] = []

            for keyword, tag in self._KEYWORD_TAG_MAP.items():
                if keyword in text:
                    if tag not in matched_tags:
                        matched_tags.append(tag)

            item.asset_tags = matched_tags[:5]
