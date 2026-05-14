from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from quant_etf_api.config.settings import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
