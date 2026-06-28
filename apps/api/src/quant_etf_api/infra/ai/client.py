"""统一 AI 客户端，基于 LiteLLM 调用 AI API。

参考 TrendRadar 项目的实现，LiteLLM 内置处理 DeepSeek 等模型的
reasoning_content 字段，无需手动适配各种输出格式变体。

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
from typing import Any

logger = logging.getLogger(__name__)


class AIClient:
    """统一的 AI 客户端，基于 LiteLLM 调用 OpenAI 兼容 API。

    LiteLLM 内部处理：
    - DeepSeek 的 reasoning_content → content 自动合并
    - 多 provider 统一接口
    - 内置重试和 fallback
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
            model: 模型标识（provider/model 格式，如 deepseek/deepseek-chat）。
            api_key: API 密钥。
            api_base: API 基础 URL（可选，自定义端点时使用）。
            temperature: 采样温度 [0, 2]。
            max_tokens: 最大生成 token 数。
            timeout: 请求超时时间（秒）。
            num_retries: 失败重试次数。
            fallback_models: 备用模型列表。
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
        from litellm import completion

        model = kwargs.pop("model", self.model)
        # 如果模型名不是 provider/model 格式且配置了 api_base，
        # 自动加上 openai/ 前缀（OpenAI 兼容接口）
        if "/" not in model and self.api_base:
            model = f"openai/{model}"
            logger.debug("自动添加 provider 前缀: %s", model)

        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.pop("temperature", self.temperature),
            "timeout": kwargs.pop("timeout", self.timeout),
            "num_retries": kwargs.pop("num_retries", self.num_retries),
        }

        if self.api_key:
            params["api_key"] = self.api_key
        if self.api_base:
            params["api_base"] = self.api_base

        max_tokens = kwargs.pop("max_tokens", self.max_tokens)
        if max_tokens and max_tokens > 0:
            params["max_tokens"] = max_tokens

        if self.fallback_models:
            params["fallbacks"] = self.fallback_models

        # 强制 JSON 输出
        params["response_format"] = {"type": "json_object"}

        # 合并剩余额外参数
        params.update(kwargs)

        try:
            response = completion(**params)
        except Exception as e:
            logger.error("LiteLLM 调用失败: %s", e)
            raise RuntimeError(f"AI API 调用失败: model={self.model}") from e

        # 提取响应内容
        msg = response.choices[0].message
        content = msg.content
        reasoning = getattr(msg, "reasoning_content", "") or ""

        if isinstance(content, list):
            content = "\n".join(
                item.get("text", str(item)) if isinstance(item, dict) else str(item)
                for item in content
            )

        # DeepSeek 思考模式下 content 可能为空，回退到 reasoning_content
        if not content and reasoning:
            logger.debug("content 为空，使用 reasoning_content (len=%d)", len(reasoning))
            content = reasoning

        return content or ""

    def chat_json(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """调用 AI 并强制返回 JSON 字典。

        解析失败时自动尝试 json_repair 修复，仍失败则返回 None。

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

    def chat_json_with_repair(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """调用 AI 获取 JSON，解析失败时请求 AI 修复。

        参考 TrendRadar 的 _retry_fix_json 模式：
        第一次解析失败 → 将 broken JSON + 错误信息发给 LLM 修复 → 再解析。

        Args:
            messages: 消息列表。
            **kwargs: 覆盖默认参数。

        Returns:
            解析后的 JSON dict，两次尝试都失败返回 None。
        """
        raw = self.chat(messages, **kwargs)
        if not raw or not raw.strip():
            logger.warning("AI 返回空响应")
            return None

        result = _extract_json(raw)
        if result is not None:
            return result

        # JSON 解析失败 → 请求 AI 修复
        logger.info("JSON 解析失败，请求 AI 修复...")
        try:
            repair_msgs = [
                {
                    "role": "system",
                    "content": (
                        "你是一个 JSON 修复助手。你会收到一段格式有误的 JSON 文本，"
                        "需要修复后返回正确的 JSON。"
                        "常见问题：字符串未转义、缺少逗号/引号、不完整的结构等。"
                        "只返回纯 JSON，不要包含 markdown 或任何说明文字。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"请修复以下 JSON 格式问题，直接返回修复后的 JSON：\n\n{raw}",
                },
            ]
            repaired_raw = self.chat(repair_msgs, temperature=0.1)
            result = _extract_json(repaired_raw)
            if result is not None:
                logger.info("AI 修复 JSON 成功")
                return result
        except Exception:
            logger.warning("AI 修复 JSON 失败", exc_info=True)

        return None

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
# JSON 提取工具
# ------------------------------------------------------------------


def _extract_json(raw: str) -> dict[str, Any] | None:
    """从 AI 响应中提取 JSON 字典。

    策略：
    1. 去除 markdown ```json ... ``` 包裹
    2. 从自然语言文本中提取 JSON 数组/对象
    3. 标准 json.loads
    4. json_repair 库修复（TrendRadar 同款方案）
    5. 正则修复（降级方案）

    Args:
        raw: AI 原始响应文本。

    Returns:
        解析后的 dict 或 list，失败返回 None。
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

    # 如果去除 markdown 后仍是空或纯文本，尝试从文本中提取 JSON
    if json_str and not (json_str.startswith("[") or json_str.startswith("{")):
        extracted = _extract_json_from_text(json_str)
        if extracted:
            json_str = extracted

    if not json_str:
        return None

    # 第一层：标准解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 第二层：json_repair 库修复（TrendRadar 同款方案）
    try:
        from json_repair import repair_json  # noqa: PLC0415

        repaired = repair_json(json_str, return_objects=True)
        if isinstance(repaired, (dict, list)):
            logger.info("JSON 修复成功（json_repair）")
            return repaired
    except Exception:
        pass

    # 第三层：正则修复常见问题（降级方案）
    try:
        repaired = _simple_json_repair(json_str)
        return json.loads(repaired)
    except Exception:
        pass

    logger.warning("JSON 解析失败，原始内容前500字符: %s", raw[:500])
    return None


def _extract_json_from_text(text: str) -> str | None:
    """从自然语言文本中提取 JSON 数组或对象。

    Args:
        text: 可能包含 JSON 的自然语言文本。

    Returns:
        提取出的 JSON 字符串，未找到返回 None。
    """
    # 尝试找到最外层的 JSON 数组
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        return text[first_bracket : last_bracket + 1]

    # 尝试找到最外层的 JSON 对象
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1]

    return None


def _simple_json_repair(text: str) -> str:
    """简单的 JSON 修复（降级方案，无需 json_repair）。

    处理常见问题：
    - 尾部逗号
    - 单引号替换为双引号
    """
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text
