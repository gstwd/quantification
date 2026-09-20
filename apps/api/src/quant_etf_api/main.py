from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from quant_etf_api.api.middleware import RequestIdMiddleware, RequestLoggingMiddleware
from quant_etf_api.domain.common.trading_calendar import TradingCalendarUnavailableError
from quant_etf_api.api.routers import (
    ai_factors,
    backtests,
    data_management,
    factors,
    health,
    indexes,
    industry,
    keyword_tags,
    market_data,
    queue as queue_router,
    robustness,
    runs,
    strategies,
    system,
)
from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.config.settings import get_settings
from quant_etf_api.factors.catalog import FactorTemplateRegistry, get_factor_template_registry
from quant_etf_api.infra.job_queue.queue import get_job_queue
from quant_etf_api.infra.scheduler import (
    get_ai_scheduler,
    get_scheduler,
)

logger = logging.getLogger(__name__)

setup_logging()
settings = get_settings()
# 因子模板注册表在进程启动时构建一次，所有请求共享同一实例
factor_template_registry: FactorTemplateRegistry = get_factor_template_registry()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 启动时恢复卡死的运行记录
    runs.recover_stuck_runs_on_startup()
    # 启动后台任务队列：先恢复卡死任务（仅心跳超时者），再启动 worker。
    # 独立 worker 进程部署下 API 重启不会误伤 worker 正在执行的任务
    job_queue = get_job_queue()
    try:
        job_queue.recover_stuck_jobs()
    except Exception:
        # 恢复失败（如 background_job 表尚未迁移）不应阻止服务启动
        logger.warning("后台任务队列恢复失败，服务继续启动", exc_info=True)
    if settings.job_queue_embedded:
        job_queue.start()
    else:
        # 独立 worker 进程部署模式：API 只入队不消费，避免两处 worker 抢任务
        logger.info("已禁用 API 进程内 worker（job_queue_embedded=false），请运行独立 worker 进程")
    if settings.schedule_enabled:
        get_scheduler().start()
    if settings.ai_analysis_enabled:
        get_ai_scheduler().start()
    # 日历预热为尽力而为：入队失败（如数据库未迁移）不阻塞启动
    try:
        # 预热交易日历缓存，防止首个请求触发慢速加载/并发崩溃
        job_queue.enqueue("warm_calendar", {}, job_key="warm_calendar")
    except Exception:
        logger.warning("启动预热任务入队失败，服务继续启动", exc_info=True)
    yield
    get_scheduler().stop()
    if settings.ai_analysis_enabled:
        get_ai_scheduler().stop()
    # 停止后台任务队列
    job_queue.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.exception_handler(TradingCalendarUnavailableError)
async def trading_calendar_unavailable_handler(
    request: Request, exc: TradingCalendarUnavailableError
) -> JSONResponse:
    """交易日历不可用统一返回 503（严格口径 C1）。

    这是对"GET 端点不返回 500 / 不返回空数组"规则的显式例外：按星期近似
    得出的日期或缺口结论比直接报错更危险，因此这里明确告知调用方"无法给出
    正确日期"，并提示可显式传 trade_date 绕过日历。
    """
    logger.warning("交易日历不可用: path=%s detail=%s", request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(system.router, prefix=settings.api_prefix)
app.include_router(data_management.router, prefix=settings.api_prefix)
app.include_router(indexes.router, prefix=settings.api_prefix)
app.include_router(industry.router, prefix=settings.api_prefix)
app.include_router(market_data.router, prefix=settings.api_prefix)
app.include_router(strategies.router, prefix=settings.api_prefix)

app.include_router(factors.router, prefix=settings.api_prefix)
app.include_router(runs.router, prefix=settings.api_prefix)
app.include_router(queue_router.router, prefix=settings.api_prefix)
app.include_router(backtests.router, prefix=settings.api_prefix)
app.include_router(robustness.router, prefix=settings.api_prefix)
app.include_router(ai_factors.router, prefix=settings.api_prefix)
app.include_router(keyword_tags.router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs"}
