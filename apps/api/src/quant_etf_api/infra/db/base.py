from __future__ import annotations
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.time import utcnow  # noqa: F401


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(
    settings.database_url,
    connect_args={"options": "-c timezone=UTC"},
    future=True,
    pool_pre_ping=True,  # 使用前检测连接是否存活，防止使用已被服务端关闭的空闲连接
    pool_recycle=1800,  # 30 分钟后自动回收连接，避免长时间空闲导致 SSL/连接被服务端断开
    pool_size=5,        # 连接池大小（默认 5），调度器并发低无需过大
    max_overflow=10,    # 连接池溢出上限
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
