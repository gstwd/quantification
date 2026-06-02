from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import Settings, get_settings
from quant_etf_api.infra.db.base import SessionLocal

if TYPE_CHECKING:
    from quant_etf_api.factors.registry import FactorRegistry


def settings_dependency() -> Settings:
    return get_settings()


def get_db() -> Generator[Session, None, None]:
    # 每次请求创建独立 Session，请求结束后关闭，避免连接泄漏
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_factor_registry() -> "FactorRegistry":
    """返回 main.py 模块级 factor_registry 全局实例。

    通过 deferred import 获取，避免模块初始化时的循环依赖。
    使用 Depends 注入而非直接 import，方便测试时替换为 mock 注册表。
    """
    from quant_etf_api.main import factor_registry  # noqa: PLC0415

    return factor_registry
