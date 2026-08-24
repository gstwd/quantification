from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from quant_etf_api.api.middleware import RequestIdMiddleware
from quant_etf_api.api.routers import (
    ai_factors,
    backtests,
    etfs,
    factors,
    health,
    indexes,
    keyword_tags,
    market_data,
    runs,
    strategies,
    system,
)
from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.config.settings import get_settings
from quant_etf_api.factors.registry import FactorRegistry, get_default_factor_registry
from quant_etf_api.infra.job_queue.queue import get_job_queue
from quant_etf_api.infra.scheduler import get_ai_scheduler, get_scheduler

logger = logging.getLogger(__name__)

setup_logging()
settings = get_settings()
# 因子注册表在进程启动时构建一次，所有请求共享同一实例
factor_registry: FactorRegistry = get_default_factor_registry()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 启动时恢复卡死的运行记录
    runs.recover_stuck_runs_on_startup()
    # 启动后台任务队列：先恢复卡死任务，再启动 worker
    job_queue = get_job_queue()
    try:
        job_queue.recover_stuck_jobs()
    except Exception:
        # 恢复失败（如 background_job 表尚未迁移）不应阻止服务启动
        logger.warning("后台任务队列恢复失败，服务继续启动", exc_info=True)
    job_queue.start()
    if settings.schedule_enabled:
        get_scheduler().start()
    if settings.ai_analysis_enabled:
        get_ai_scheduler().start()
    # 启动补全与日历预热均为尽力而为：入队失败（如数据库未迁移）不阻塞启动
    try:
        if settings.startup_fill_enabled:
            job_queue.enqueue("startup_fill", {}, job_key="startup_fill")
        # 预热交易日历缓存，防止首个请求触发慢速加载/并发崩溃
        job_queue.enqueue("warm_calendar", {}, job_key="warm_calendar")
    except Exception:
        logger.warning("启动补全/日历预热任务入队失败，服务继续启动", exc_info=True)
    yield
    get_scheduler().stop()
    if settings.ai_analysis_enabled:
        get_ai_scheduler().stop()
    # 停止后台任务队列
    job_queue.stop()


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

app.include_router(factors.router, prefix=settings.api_prefix)
app.include_router(runs.router, prefix=settings.api_prefix)
app.include_router(backtests.router, prefix=settings.api_prefix)
app.include_router(ai_factors.router, prefix=settings.api_prefix)
app.include_router(keyword_tags.router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs"}
