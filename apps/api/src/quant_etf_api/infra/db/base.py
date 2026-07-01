from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from quant_etf_api.config.settings import get_settings


def utcnow() -> datetime:
    """返回当前 UTC 时间的 naive datetime。

    刻意去除 tzinfo，避免 psycopg 将 aware datetime 插入
    ``timestamp without time zone`` 列时做时区转换。
    所有 ORM 默认值和业务代码中需要写入数据库的时间戳都应使用此函数。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
