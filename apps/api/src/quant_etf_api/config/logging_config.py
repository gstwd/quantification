"""结构化日志配置。

提供 setup_logging() 入口函数：
- 控制台 handler：人类可读格式（时间 + 级别 + logger + 消息）
- 文件 handler（可选）：JSON 行格式，支持 RotatingFileHandler（10 MB × 5）
- 第三方库日志级别抑制
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler


class JsonFormatter(logging.Formatter):
    """JSON 行格式化器，每行一条结构化日志记录。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        req_id: str | None = getattr(record, "request_id", None)
        if req_id:
            log_entry["request_id"] = req_id
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging() -> None:
    """配置结构化日志：控制台 + 可选 JSON 文件。

    从 Settings 读取 log_level / log_file，设置 root logger 的 handler。
    第三方库（uvicorn、sqlalchemy、akshare、httpx）日志级别统一设为 WARNING。
    """
    from quant_etf_api.config.settings import get_settings

    settings = get_settings()
    level = _parse_level(settings.log_level)

    handlers: list[logging.Handler] = []

    # 控制台 handler —— 人类可读格式
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handlers.append(console)

    # 文件 handler —— JSON 格式（可选）
    if settings.log_file:
        file_handler = RotatingFileHandler(
            settings.log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)

    # 注入 request_id filter
    from quant_etf_api.api.middleware import RequestIdFilter

    root = logging.getLogger()
    root.addFilter(RequestIdFilter())

    # 抑制第三方库日志噪音
    for lib in ("uvicorn.access", "sqlalchemy.engine", "akshare", "httpx"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def _parse_level(name: str) -> int:
    """将配置字符串转为 logging 级别常量，非法值降级为 INFO。"""
    try:
        return getattr(logging, name.upper())
    except AttributeError:
        return logging.INFO
