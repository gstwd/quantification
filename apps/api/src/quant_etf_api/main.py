from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from quant_etf_api.api.routers import etfs, health, market_data, runs, signals, strategies, system
from quant_etf_api.config.settings import get_settings
from quant_etf_api.plugins.registry import StrategyRegistry, build_default_registry

settings = get_settings()
# 策略注册表在进程启动时构建一次，所有请求共享同一实例
registry: StrategyRegistry = build_default_registry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


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
app.include_router(market_data.router, prefix=settings.api_prefix)
app.include_router(strategies.router, prefix=settings.api_prefix)
app.include_router(signals.router, prefix=settings.api_prefix)
app.include_router(runs.router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs"}
