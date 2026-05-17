import logging
import threading
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from quant_etf_api.api.routers import (
    backtests,
    etfs,
    health,
    indexes,
    market_data,
    runs,
    signals,
    strategies,
    system,
)
from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.scheduler import get_scheduler
from quant_etf_api.plugins.registry import StrategyRegistry, build_default_registry

logger = logging.getLogger(__name__)

settings = get_settings()
# 策略注册表在进程启动时构建一次，所有请求共享同一实例
registry: StrategyRegistry = build_default_registry()


def _trigger_startup_fill() -> None:
    """在后台启动一次数据缺口补全，不阻塞服务器启动。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    def _bg() -> None:
        db = SessionLocal()
        try:
            summary = RunService(db).create_run("startup_fill", None, date.today())
            IngestService(db).run_startup_fill(summary.run_id)
        except Exception:
            logger.warning("启动补全失败", exc_info=True)
        finally:
            db.close()

    threading.Thread(target=_bg, daemon=True, name="startup-fill").start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.schedule_enabled:
        get_scheduler().start()
    if settings.startup_fill_enabled:
        _trigger_startup_fill()
    yield
    get_scheduler().stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(system.router, prefix=settings.api_prefix)
app.include_router(etfs.router, prefix=settings.api_prefix)
app.include_router(indexes.router, prefix=settings.api_prefix)
app.include_router(market_data.router, prefix=settings.api_prefix)
app.include_router(strategies.router, prefix=settings.api_prefix)
app.include_router(signals.router, prefix=settings.api_prefix)
app.include_router(runs.router, prefix=settings.api_prefix)
app.include_router(backtests.router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs"}
