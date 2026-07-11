"""多渠道 LLM 配置系统。

通过 LLM_CHANNELS 环境变量支持同时配置多个 LLM 提供者渠道，
每个渠道有独立的 base_url、api_key、models 等配置。

格式::

    LLM_CHANNELS=deepseek,aihubmix
    LLM_DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
    LLM_DEEPSEEK_API_KEY=sk-xxx
    LLM_DEEPSEEK_MODELS=deepseek-chat,deepseek-reasoner
    LLM_AIHUBMIX_BASE_URL=https://aihubmix.com/v1
    LLM_AIHUBMIX_API_KEY=sk-yyy
    LLM_AIHUBMIX_MODELS=gpt-4o,claude-3.5-sonnet
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LLMChannelConfig:
    """单个 LLM 渠道配置。

    Attributes:
        name: 渠道名称（如 "deepseek"、"aihubmix"）。
        base_url: API Base URL。
        api_key: API Key。
        models: 可用模型列表。
        default_model: 该渠道的默认模型（取 models[0]）。
        timeout: 请求超时秒数。
        max_tokens: 最大生成 token 数。
        temperature: 采样温度。
    """

    name: str
    base_url: str | None = None
    api_key: str | None = None
    models: list[str] = field(default_factory=list)
    default_model: str = ""
    timeout: int = 60
    max_tokens: int = 4096
    temperature: float = 0.3

    @property
    def is_configured(self) -> bool:
        """该渠道是否已完成基本配置（有 API Key 且有模型）。"""
        return bool(self.api_key and (self.models or self.default_model))


class LLMChannelRegistry:
    """LLM 渠道注册表。

    解析 LLM_CHANNELS 环境变量（逗号分隔的渠道名），
    为每个渠道从环境变量加载独立配置。

    渠道配置通过 LLM_{NAME}_{FIELD} 格式的环境变量注入，
    其中 {NAME} 为大写渠道名，{FIELD} 为大写字段名。
    """

    def __init__(self, channels_str: str = "") -> None:
        """从逗号分隔的渠道名列表初始化。

        Args:
            channels_str: LLM_CHANNELS 环境变量值（如 "deepseek,aihubmix"）。
        """
        self._channels: dict[str, LLMChannelConfig] = {}
        if not channels_str:
            return

        names = [n.strip() for n in channels_str.split(",") if n.strip()]
        for name in names:
            config = self._load_channel_config(name)
            if config.is_configured:
                self._channels[name] = config
                logger.info(
                    "LLM 渠道已加载: %s (models=%s)",
                    name,
                    config.models,
                )
            else:
                logger.debug(
                    "LLM 渠道 %s 未完整配置（缺少 API Key 或模型列表），跳过",
                    name,
                )

    @property
    def channels(self) -> dict[str, LLMChannelConfig]:
        """返回所有已配置的渠道。"""
        return dict(self._channels)

    @property
    def is_configured(self) -> bool:
        """是否至少有一个渠道配置完成。"""
        return len(self._channels) > 0

    def get_channel(self, name: str) -> LLMChannelConfig | None:
        """按名称获取渠道配置。

        Args:
            name: 渠道名称。

        Returns:
            LLMChannelConfig 或 None。
        """
        return self._channels.get(name)

    def get_default_channel(self) -> LLMChannelConfig | None:
        """返回第一个已配置的渠道（作为默认渠道）。

        Returns:
            LLMChannelConfig 或 None。
        """
        if self._channels:
            return next(iter(self._channels.values()))
        return None

    @classmethod
    def from_env(cls) -> LLMChannelRegistry:
        """从环境变量构建注册表。

        读取 LLM_CHANNELS 环境变量并解析。

        Returns:
            LLMChannelRegistry 实例。
        """
        channels_str = os.getenv("LLM_CHANNELS", "")
        return cls(channels_str)

    @staticmethod
    def _load_channel_config(name: str) -> LLMChannelConfig:
        """从环境变量加载单个渠道的配置。

        环境变量格式: LLM_{NAME}_{FIELD}
        例如 LLM_DEEPSEEK_BASE_URL、LLM_DEEPSEEK_API_KEY。

        Args:
            name: 渠道名称（如 "deepseek"）。

        Returns:
            LLMChannelConfig 实例。
        """
        prefix = f"LLM_{name.upper()}_"

        def _env(key: str, default: Any = None) -> Any:
            return os.getenv(f"{prefix}{key}", default)

        models_str = _env("MODELS", "")
        models = [m.strip() for m in models_str.split(",") if m.strip()] if models_str else []

        return LLMChannelConfig(
            name=name,
            base_url=_env("BASE_URL") or None,
            api_key=_env("API_KEY") or None,
            models=models,
            default_model=models[0] if models else "",
            timeout=int(_env("TIMEOUT", "60")),
            max_tokens=int(_env("MAX_TOKENS", "4096")),
            temperature=float(_env("TEMPERATURE", "0.3")),
        )
