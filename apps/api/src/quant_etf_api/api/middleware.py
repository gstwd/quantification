"""请求 ID 中间件。

- 从 X-Request-ID 请求头读取或自动生成 UUID
- 注入 ContextVar，供日志和下游服务使用
- 写入响应头 X-Request-ID
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Starlette 中间件，为每个请求注入唯一的请求 ID。"""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[type-arg]
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request_id_var.set(req_id)
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


class RequestIdFilter(logging.Filter):
    """logging Filter，将 ContextVar 中的 request_id 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"  # type: ignore[attr-defined]
        return True


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """请求计时与异常日志中间件。

    主要目标：
    - 让慢查询、异常与客户端中断在后端日志中可见（uvicorn.access 被抑制）；
    - 对 /api/industry 研究接口始终打印开始/完成与耗时，便于定位“页面 30s
      断开但服务端仍在计算”的问题；
    - 其余接口仅记录慢请求（>= 10s）、5xx 或异常。
    """

    # 慢请求阈值（毫秒）
    SLOW_MS: int = 10_000

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[type-arg]
        """记录请求耗时与结果，耗时或异常超阈值时输出告警日志。"""
        logger = logging.getLogger("quant_etf_api.api.request")
        path = request.url.path
        query = request.url.query
        method = request.method
        research = path.startswith("/api/industry/")
        if method == "OPTIONS":
            return await call_next(request)
        started = time.perf_counter()
        if research:
            logger.info(
                "开始处理请求 method=%s path=%s query=%s",
                method,
                path,
                query,
            )
        try:
            response: Response = await call_next(request)
        except asyncio.CancelledError:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.warning(
                "请求被客户端取消或连接中断 method=%s path=%s query=%s elapsed_ms=%.0f",
                method,
                path,
                query,
                elapsed_ms,
            )
            raise
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "请求处理异常 method=%s path=%s query=%s elapsed_ms=%.0f",
                method,
                path,
                query,
                elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        status = response.status_code
        if research:
            logger.info(
                "请求完成 method=%s path=%s status=%s elapsed_ms=%.0f",
                method,
                path,
                status,
                elapsed_ms,
            )
        elif elapsed_ms >= self.SLOW_MS or status >= 500:
            logger.warning(
                "慢请求或服务端错误 method=%s path=%s status=%s elapsed_ms=%.0f query=%s",
                method,
                path,
                status,
                elapsed_ms,
                query,
            )
        response.headers["X-Request-ID"] = request_id_var.get()
        return response
