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


@lru_cache
def get_settings() -> Settings:
    return Settings()
