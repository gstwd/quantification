from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUANT_ETF_", env_file=".env", extra="ignore")

    app_name: str = "Quant ETF Research Platform"
    app_env: str = Field(default="development")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    api_prefix: str = Field(default="/api")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/quant_etf"
    )

    # 定时调度
    schedule_enabled: bool = Field(default=True, description="是否启用每日收盘后自动数据摄取")
    schedule_time: str = Field(
        default="17:30", description="每日自动摄取触发时间（HH:MM），默认收盘后 17:30"
    )
    startup_fill_enabled: bool = Field(
        default=True, description="系统启动时是否自动检查并补全数据缺口"
    )
    ai_analysis_enabled: bool = Field(
        default=True, description="是否在日频调度中自动触发 AI 舆情分析"
    )

    # 日志配置
    log_level: str = Field(default="INFO", description="日志级别：DEBUG / INFO / WARNING / ERROR")
    log_file: str | None = Field(
        default=None, description="JSON 日志文件路径，留空则仅输出到控制台"
    )

    # LLM 配置（可选，不配置则 AI 分析功能不可用）
    llm_api_key: str | None = Field(default=None, description="LLM API Key")
    llm_base_url: str | None = Field(default=None, description="LLM API Base URL（OpenAI 兼容接口）")
    llm_model: str = Field(default="deepseek/deepseek-chat", description="默认模型标识（LiteLLM 格式：provider/model）")
    llm_max_tokens: int = Field(default=2000, description="AI 分析最大输出 token 数")
    llm_temperature: float = Field(default=0.3, description="AI 分析温度参数")
    llm_timeout_seconds: int = Field(default=60, description="LLM 请求超时时间")


@lru_cache
def get_settings() -> Settings:
    return Settings()
