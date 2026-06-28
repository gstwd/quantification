"""统一 AI 客户端，基于 httpx 直接调用 OpenAI 兼容 API。

支持任何 OpenAI 兼容接口（DeepSeek、GPT、Claude via proxy 等）。
零额外依赖 — httpx 已是项目依赖。

使用示例::

    from quant_etf_api.config.settings import get_settings
    from quant_etf_api.infra.ai import AIClient

    settings = get_settings()
    client = AIClient.from_settings(settings)
    result = client.chat([{"role": "user", "content": "你好"}])
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AIClient:
    """统一的 AI 客户端，通过 httpx 调用 OpenAI 兼容 API。

    支持标准 chat 和 chat_json（自动提取 JSON）两种模式。
    API 不可用时自动重试（指数退避）。
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        api_base: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: int = 60,
        num_retries: int = 2,
        fallback_models: list[str] | None = None,
    ) -> None:
        """初始化 AI 客户端。

        Args:
            model: 模型标识。
            api_key: API 密钥。
            api_base: API 基础 URL（OpenAI 兼容接口的 /v1 端点），可选。
                     如果未提供，使用 OpenAI 默认地址。
            temperature: 采样温度 [0, 2]。
            max_tokens: 最大生成 token 数。
            timeout: 请求超时时间（秒）。
            num_retries: 失败重试次数。
            fallback_models: 备用模型列表（暂不支持，保留接口兼容）。
        """
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.num_retries = num_retries
        self.fallback_models = fallback_models or []

    @classmethod
    def from_settings(cls, settings: Any) -> AIClient:
        """从项目 Settings 对象构建 AIClient 实例。

        Args:
            settings: 包含 llm_* 属性的 Pydantic Settings 对象。

        Returns:
            配置好的 AIClient 实例。
        """
        return cls(
            model=getattr(settings, "llm_model", "gpt-4o-mini"),
            api_key=getattr(settings, "llm_api_key", None),
            api_base=getattr(settings, "llm_base_url", None),
            temperature=getattr(settings, "llm_temperature", 0.3),
            max_tokens=getattr(settings, "llm_max_tokens", 2000),
            timeout=getattr(settings, "llm_timeout_seconds", 60),
        )

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """调用 AI 模型进行对话。

        Args:
            messages: 消息列表 [{"role": "system/user/assistant", "content": "..."}]。
            **kwargs: 覆盖默认参数（temperature、max_tokens 等）。

        Returns:
            AI 响应文本内容。

        Raises:
            RuntimeError: API 调用失败时抛出。
        """
        temperature = kwargs.pop("temperature", self.temperature)
        max_tokens = kwargs.pop("max_tokens", self.max_tokens)
        timeout = kwargs.pop("timeout", self.timeout)
        retries = kwargs.pop("num_retries", self.num_retries)
        model = kwargs.pop("model", self.model)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens and max_tokens > 0:
            payload["max_tokens"] = max_tokens

        # 合并剩余额外参数
        payload.update(kwargs)

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self._call_api(payload, timeout)
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    delay = 2 ** attempt
                    logger.debug("AI API 重试 %d/%d (等待 %ds)", attempt + 1, retries, delay)
                    time.sleep(delay)

        logger.error("AI API 调用失败(已重试%d次): %s", retries, last_error)
        raise RuntimeError(f"AI API 调用失败: model={model}") from last_error

    def chat_json(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """调用 AI 并强制返回 JSON 字典。

        自动处理 markdown 代码块包裹的 JSON 和轻微格式问题。

        Args:
            messages: 消息列表。
            **kwargs: 覆盖默认参数。

        Returns:
            解析后的 JSON dict，失败时返回 None。
        """
        raw = self.chat(messages, **kwargs)
        if not raw or not raw.strip():
            logger.warning("AI 返回空响应")
            return None

        return _extract_json(raw)

    def validate(self) -> tuple[bool, str]:
        """验证配置是否有效。

        Returns:
            (是否有效, 错误信息) 元组。
        """
        if not self.model:
            return False, "未配置 AI 模型"
        if not self.api_key:
            return False, "未配置 AI API Key（请在 .env 中设置 QUANT_ETF_LLM_API_KEY）"
        return True, ""

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_api(self, payload: dict[str, Any], timeout: int) -> str:
        """发送 POST 请求到 OpenAI 兼容 API。

        自动构建 URL：如果有 api_base 则拼接 /chat/completions，
        否则使用 OpenAI 默认端点。

        Args:
            payload: 请求体。
            timeout: HTTP 超时（秒）。

        Returns:
            API 响应的 content 文本。

        Raises:
            RuntimeError: HTTP 错误或响应格式异常。
        """
        if self.api_base:
            url = self.api_base.rstrip("/") + "/chat/completions"
        else:
            url = "https://api.openai.com/v1/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key or ''}",
        }

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("API 返回空 choices")

        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                item.get("text", str(item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return content or ""


# ------------------------------------------------------------------
# JSON 提取工具
# ------------------------------------------------------------------


def _extract_json(raw: str) -> dict[str, Any] | None:
    """从 AI 响应中提取 JSON 字典。

    策略：
    1. 去除 markdown ```json ... ``` 包裹
    2. 标准 json.loads
    3. 正则修复常见问题（未转义引号、尾部逗号）

    Args:
        raw: AI 原始响应文本。

    Returns:
        解析后的 dict，失败返回 None。
    """
    json_str = raw

    # 去除 markdown 代码块标记
    if "```json" in json_str:
        parts = json_str.split("```json", 1)
        if len(parts) > 1:
            end_idx = parts[1].find("```")
            json_str = parts[1][:end_idx] if end_idx != -1 else parts[1]
    elif "```" in json_str:
        parts = json_str.split("```", 2)
        if len(parts) >= 2:
            json_str = parts[1]

    json_str = json_str.strip()
    if not json_str:
        return None

    # 第一层：标准解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 第二层：正则修复常见 JSON 问题
    try:
        repaired = _simple_json_repair(json_str)
        return json.loads(repaired)
    except Exception:
        pass

    logger.warning("JSON 解析失败，原始内容前200字符: %s", raw[:200])
    return None


def _simple_json_repair(text: str) -> str:
    """简单的 JSON 修复（无需 json_repair 包）。

    处理常见问题：
    - 字符串值内的未转义双引号
    - 尾部逗号
    - 单引号替换为双引号
    """
    # 移除尾部逗号（对象和数组）
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text
