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
你是金融新闻情绪分析器。你的全部输出将被 JSON 解析器直接读取，不能包含任何其他文字。

输出格式（严格唯一格式，不可偏离）：
{"results":[{"index":0,"title":"新闻标题","sentiment_score":0.5,"relevance_score":0.8,"asset_tags":["科技","AI"],"topics":["人工智能","大模型"],"summary":"一句话摘要（50字以内）"}]}

评分标准：
- sentiment_score [-1.0, 1.0]：正=利好，负=利空。>0.8重大利好，0.3~0.7温和利好，±0.3中性，-0.7~-0.3温和利空，<-0.8重大利空
- relevance_score [0, 1.0]：>0.8直接冲击A股，0.5~0.7间接影响，<0.5弱关联
- 无法判断情绪时 sentiment_score=0.0
- 每条新闻必须有对应条目，用 index 保持输入顺序
- asset_tags 从提供的标签列表中选取（不编造），topics 为关键词短语
- 无关社会娱乐新闻也要输出（sentiment=0, relevance=0, asset_tags=[], topics=[]）

[user]
当前日期：${current_date}
市场背景：${market_context}
可用资产标签：${available_tags}

新闻列表（每行格式：[index] [来源] 标题 | 排名信息）：
${news_list}"""

DEFAULT_CLASSIFY_PROMPT = """[system]
你是金融新闻分类器。你的全部输出将被 JSON 解析器直接读取，不能包含任何其他文字。

输出格式：{"results":[{"index":0,"tags":["科技","AI"],"confidence":0.9}]}

规则：
- 每条新闻 0-3 个标签，从给定列表选择
- 完全不相关则 tags=[]
- 用 index 保持输入顺序，每条新闻都必须输出

[user]
可用标签：${available_tags}
新闻列表：
${news_list}"""


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
