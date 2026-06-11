from __future__ import annotations

import logging
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from quant_etf_api.api.middleware import RequestIdMiddleware
from quant_etf_api.api.routers import (
    backtests,
    etfs,
    factors,
    health,
    indexes,
    market_data,
    runs,
    signals,
    strategies,
    system,
)
from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.config.settings import get_settings
from quant_etf_api.factors.registry import FactorRegistry, get_default_factor_registry
from quant_etf_api.infra.scheduler import get_scheduler

logger = logging.getLogger(__name__)

setup_logging()
settings = get_settings()
# 因子注册表在进程启动时构建一次，所有请求共享同一实例
factor_registry: FactorRegistry = get_default_factor_registry()


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
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 启动时恢复卡死的运行记录
    runs.recover_stuck_runs_on_startup()
    if settings.schedule_enabled:
        get_scheduler().start()
    if settings.startup_fill_enabled:
        _trigger_startup_fill()
    yield
    get_scheduler().stop()
    # 关闭共享后台任务线程池
    from quant_etf_api.api.executor import get_bg_executor
    get_bg_executor().shutdown(wait=False, cancel_futures=True)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)
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
app.include_router(factors.router, prefix=settings.api_prefix)
app.include_router(runs.router, prefix=settings.api_prefix)
app.include_router(backtests.router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs"}
