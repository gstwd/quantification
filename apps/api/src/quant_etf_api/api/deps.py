from collections.abc import Generator

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import Settings, get_settings
from quant_etf_api.infra.db.base import SessionLocal


def settings_dependency() -> Settings:
    return get_settings()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
