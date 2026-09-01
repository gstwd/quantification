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
        default="02:30", description="每日自动摄取触发时间（HH:MM），默认收盘后 02:30"
    )
    startup_fill_enabled: bool = Field(
        default=True, description="系统启动时是否自动检查并补全数据缺口"
    )
    ai_analysis_enabled: bool = Field(
        default=True, description="是否在日频调度中自动触发 AI 舆情分析"
    )
    ai_schedule_time: str = Field(
        default="23:30", description="AI 舆情分析触发时间（HH:MM），默认夜里 23:30，覆盖当天全部新闻"
    )

    # 后台任务队列
    job_queue_workers: int = Field(
        default=4, ge=1, description="后台任务队列 worker 线程数"
    )
    job_poll_interval_seconds: float = Field(
        default=1.0, gt=0, description="任务队列空转时的轮询间隔（秒）"
    )

    # 日志配置
    log_level: str = Field(default="INFO", description="日志级别：DEBUG / INFO / WARNING / ERROR")
    log_file: str | None = Field(
        default=None, description="JSON 日志文件路径，留空则仅输出到控制台"
    )

    # LLM 配置（可选，不配置则 AI 分析功能不可用）
    llm_api_key: str | None = Field(default=None, description="LLM API Key")
    llm_base_url: str | None = Field(
        default=None, description="LLM API Base URL（OpenAI 兼容接口）"
    )
    llm_model: str = Field(
        default="deepseek/deepseek-chat", description="默认模型标识（LiteLLM 格式：provider/model）"
    )
    llm_max_tokens: int = Field(default=4096, description="AI 分析最大输出 token 数")
    llm_temperature: float = Field(default=0.3, description="AI 分析温度参数")
    llm_timeout_seconds: int = Field(default=60, description="LLM 请求超时时间")
    ai_max_analysis_items: int = Field(
        default=150,
        description="单次 AI 分析最大新闻条数，超出部分仅计算关注度不调用 LLM",
    )

    # ========== 新闻多源搜索配置 ==========

    tavily_api_keys: list[str] = Field(
        default_factory=list,
        description="Tavily 搜索 API Key 列表（支持多 Key 轮询）",
    )
    bocha_api_keys: list[str] = Field(
        default_factory=list,
        description="Bocha AI 搜索 API Key 列表（中文搜索优化）",
    )
    brave_api_keys: list[str] = Field(
        default_factory=list,
        description="Brave Search API Key 列表",
    )
    serpapi_api_keys: list[str] = Field(
        default_factory=list,
        description="SerpAPI Key 列表（Google News 搜索）",
    )
    anspire_api_keys: list[str] = Field(
        default_factory=list,
        description="Anspire Search API Key 列表（实时智能搜索）",
    )
    searxng_urls: list[str] = Field(
        default_factory=list,
        description="SearXNG 自建实例 URL 列表（无配额兜底搜索）",
    )

    # ========== 社交媒体情绪配置 ==========

    social_sentiment_api_key: str | None = Field(
        default=None,
        description="社交媒体情绪 API Key（api.adanos.org），用于美股情绪分析",
    )

    # ========== 多数据源指数日线摄取 ==========

    tushare_token: str | None = Field(
        default=None,
        description="Tushare Pro API Token，配置后启用 tushare 指数日线数据源",
    )
    index_daily_source_order: str = Field(
        default="efinance,akshare,tushare,pytdx,baostock",
        description=(
            "指数日线多数据源优先级（逗号分隔），可选 efinance/akshare/tushare/pytdx/baostock；"
            "tushare 未配置 Token 时自动跳过"
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
