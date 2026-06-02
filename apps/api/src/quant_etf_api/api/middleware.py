"""请求 ID 中间件。

- 从 X-Request-ID 请求头读取或自动生成 UUID
- 注入 ContextVar，供日志和下游服务使用
- 写入响应头 X-Request-ID
"""

from __future__ import annotations

import logging
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
