"""统一重试策略，应用于所有数据源客户端。

提供装饰器和上下文管理器两种使用方式。
参数可通过环境变量配置：AKSHARE_RETRY_MAX_ATTEMPTS / AKSHARE_RETRY_BASE_DELAY。
"""

from __future__ import annotations

import logging
import os
import time
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 默认重试参数
_DEFAULT_MAX_ATTEMPTS = int(os.getenv("AKSHARE_RETRY_MAX_ATTEMPTS", "3"))
_DEFAULT_BASE_DELAY = float(os.getenv("AKSHARE_RETRY_BASE_DELAY", "1.0"))
_DEFAULT_BACKOFF = 2.0
_DEFAULT_MAX_DELAY = 30.0

# 可重试的异常类型
_RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    ConnectionResetError,
    TimeoutError,
    OSError,
)


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否可重试。

    Args:
        exc: 捕获的异常。

    Returns:
        True 表示应该重试。
    """
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    # AttributeError 可能来自 AkShare API 临时数据异常
    if isinstance(exc, AttributeError):
        return True
    return False


def with_retry(
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
    backoff: float = _DEFAULT_BACKOFF,
    max_delay: float = _DEFAULT_MAX_DELAY,
    retryable: tuple[type[Exception], ...] = _RETRYABLE_EXCEPTIONS,
) -> Callable:
    """重试装饰器，指数退避策略。

    用法::

        @with_retry(max_attempts=3, base_delay=1.0)
        def fetch_data():
            ...

    Args:
        max_attempts: 最大尝试次数（含首次）。
        base_delay: 首次重试延迟（秒）。
        backoff: 退避倍数，每次重试延迟 × backoff。
        max_delay: 最大延迟上限（秒）。
        retryable: 可重试的异常类型元组。

    Returns:
        装饰后的函数。
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exception = exc
                    if attempt >= max_attempts:
                        logger.error(
                            "%s 重试 %d 次后仍失败: %s",
                            func.__name__,
                            max_attempts,
                            exc,
                        )
                        raise

                    if not _is_retryable(exc):
                        # 不可重试的异常直接抛出
                        raise

                    delay = min(base_delay * (backoff ** (attempt - 1)), max_delay)
                    logger.warning(
                        "%s 第 %d/%d 次尝试失败: %s，%.1fs 后重试",
                        func.__name__,
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
            # 理论上不会执行到这里
            if last_exception:
                raise last_exception
            return None

        return wrapper

    return decorator
