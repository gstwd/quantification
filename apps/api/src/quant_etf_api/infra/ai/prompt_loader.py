"""Prompt 模板加载器。

从 TrendRadar 项目的 prompt_loader 抽象而来，支持加载 [system]/[user]
分离的提示词模板文件，并支持变量插值。

内置默认 Prompt 用于：
- 新闻情绪分析
- 资产标签分类
- 新闻摘要

使用示例::

    from quant_etf_api.infra.ai import PromptLoader

    loader = PromptLoader(prompts_dir="ai_factors/prompts")
    system, user = loader.load("sentiment_analysis")
"""

from __future__ import annotations

import logging
from pathlib import Path
from string import Template
from typing import ClassVar

logger = logging.getLogger(__name__)

# ---- 默认 Prompt 模板（无需外部文件即可工作） ----

DEFAULT_SENTIMENT_PROMPT = """[system]
你是一名专业的量化金融分析师，擅长从财经新闻中提取结构化信号。

你需要对每条新闻进行情绪分析和资产关联判断：

情绪评分标准（sentiment_score）：
- +0.8 ~ +1.0：重大利好（政策强刺激、业绩超预期、重大技术突破）
- +0.3 ~ +0.7：温和利好（行业景气、订单增加、技术升级）
- -0.3 ~ +0.3：中性或信息不足
- -0.7 ~ -0.3：温和利空（业绩下滑、竞争加剧、监管趋严）
- -1.0 ~ -0.8：重大利空（政策打压、暴雷事件、行业危机）

市场相关度评分标准（relevance_score）：
- 0.8 ~ 1.0：直接影响A股特定行业或标的
- 0.5 ~ 0.7：间接影响或需持续跟踪
- 0.0 ~ 0.4：与A股市场关联较弱

资产标签（asset_tags）：为每条新闻打上关联的资产标签，如：
- 指数：000300（沪深300）、000905（中证500）、000016（上证50）、399006（创业板指）
- 行业：金融、科技、消费、医药、新能源、半导体、军工、地产
- 概念：AI、芯片、新能源车、光伏、数字经济、机器人

请严格按照 JSON 格式输出，每条新闻一个对象，所有新闻放入一个数组：

```json
[
  {
    "index": 0,
    "title": "新闻标题",
    "sentiment_score": 0.5,
    "relevance_score": 0.8,
    "asset_tags": ["科技", "AI"],
    "topics": ["人工智能", "大模型"],
    "summary": "一句话摘要（50字以内）"
  }
]
```

要求：
- 忽略与A股/中国经济无关的社会娱乐新闻
- 对于无法判断情绪的新闻，sentiment_score 设为 0.0
- 资产标签从给定的标签列表中选择，不要编造新标签
- 每条新闻都必须输出，保持原始顺序

[user]
当前日期：${current_date}
市场背景：${market_context}

可用资产标签：${available_tags}

请分析以下新闻列表：

${news_list}"""

DEFAULT_CLASSIFY_PROMPT = """[system]
你是一名金融新闻分类专家。你需要将新闻标题分类到预定义的资产/行业标签中。

规则：
1. 每条新闻可以关联 0-3 个标签
2. 如果新闻完全不相关，返回空数组
3. 只使用提供的标签列表，不创造新标签
4. 同时给出分类置信度（0-1）

[user]
可用标签：${available_tags}

新闻列表：
${news_list}

请输出 JSON 数组：
[{"index": 0, "tags": ["科技", "AI"], "confidence": 0.9}, ...]"""


class PromptLoader:
    """Prompt 模板加载器，支持内置默认模板和外部文件两种方式。

    外部文件优先级 > 内置默认模板。
    """

    _builtin: ClassVar[dict[str, str]] = {
        "sentiment_analysis": DEFAULT_SENTIMENT_PROMPT,
        "news_classify": DEFAULT_CLASSIFY_PROMPT,
    }

    def __init__(self, prompts_dir: str | Path | None = None) -> None:
        """初始化 Prompt 加载器。

        Args:
            prompts_dir: 外部 prompt 模板目录路径，可选。
                         如果目录中有同名文件，会覆盖内置模板。
        """
        self._prompts_dir = Path(prompts_dir) if prompts_dir else None

    def load(self, name: str) -> tuple[str, str]:
        """加载指定名称的 prompt 模板，返回 (system_prompt, user_prompt)。

        查找顺序：外部文件 > 内置模板。

        Args:
            name: 模板名称（不含扩展名），如 "sentiment_analysis"。

        Returns:
            (system_prompt, user_prompt) 元组。如果模板无 system 段，
            system_prompt 为空字符串。

        Raises:
            FileNotFoundError: 外部文件不存在且无内置模板。
            ValueError: 模板中没有 [system] 或 [user] 段。
        """
        raw = self._load_raw(name)
        return _parse_prompt_template(raw)

    def _load_raw(self, name: str) -> str:
        """加载原始模板文本。"""
        # 优先外部文件
        if self._prompts_dir:
            file_path = self._prompts_dir / f"{name}.txt"
            if file_path.exists():
                logger.debug("加载外部 prompt: %s", file_path)
                return file_path.read_text(encoding="utf-8")

        # 回退内置
        builtin = self._builtin.get(name)
        if builtin:
            logger.debug("使用内置 prompt: %s", name)
            return builtin

        raise FileNotFoundError(f"Prompt 模板 '{name}' 不存在（外部文件或内置均未找到）")

    def render(
        self,
        name: str,
        variables: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        """加载模板并填充变量，返回 (system_prompt, user_prompt)。

        Args:
            name: 模板名称。
            variables: 变量映射 {var_name: value}。

        Returns:
            (system_prompt, user_prompt) 元组。
        """
        system, user = self.load(name)
        if variables:
            system = _safe_substitute(system, variables)
            user = _safe_substitute(user, variables)
        return system, user


def load_prompt(name: str) -> tuple[str, str]:
    """快捷函数：使用内置模板加载 prompt。

    Args:
        name: 模板名称。

    Returns:
        (system_prompt, user_prompt) 元组。
    """
    return PromptLoader().load(name)


def _safe_substitute(template: str, variables: dict[str, str]) -> str:
    """安全变量替换，使用 ${var} 语法，未提供变量保留原样。

    Args:
        template: 模板字符串。
        variables: 变量映射。

    Returns:
        替换后的字符串。
    """
    return Template(template).safe_substitute(variables)


def _parse_prompt_template(raw: str) -> tuple[str, str]:
    """解析 [system]/[user] 格式的 prompt 模板。

    文件格式：
        [system]
        system prompt 内容...

        [user]
        user prompt 内容...

    Args:
        raw: 原始模板文本。

    Returns:
        (system_prompt, user_prompt) 元组。
    """
    system_prompt = ""
    user_prompt = ""

    system_start = raw.find("[system]")
    user_start = raw.find("[user]")

    if system_start == -1 and user_start == -1:
        # 没有分段标记，整个文本作为 user prompt
        return "", raw.strip()

    if system_start != -1:
        system_end = user_start if user_start != -1 else len(raw)
        system_prompt = raw[system_start + len("[system]"):system_end].strip()

    if user_start != -1:
        user_prompt = raw[user_start + len("[user]"):].strip()

    return system_prompt, user_prompt
