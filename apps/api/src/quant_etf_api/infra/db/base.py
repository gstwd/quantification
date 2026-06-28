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
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
