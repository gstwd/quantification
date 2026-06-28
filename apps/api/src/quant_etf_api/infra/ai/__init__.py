"""AI 基础设施层：提供独立可复用的 AI 客户端和 Prompt 加载能力。

本包不依赖任何项目其他模块，仅依赖 config/settings。
"""

from quant_etf_api.infra.ai.client import AIClient
from quant_etf_api.infra.ai.prompt_loader import PromptLoader, load_prompt

__all__ = ["AIClient", "PromptLoader", "load_prompt"]
