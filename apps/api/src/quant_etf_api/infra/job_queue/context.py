"""后台任务执行上下文：向处理器暴露当前任务并传递协作取消信号。

回测等长任务会在安全检查点（每日循环起点 / checkpoint 提交）调用
``ensure_not_cancelled()``；取消标记只在数据库里，因此这里按任务做短 TTL
缓存，避免长循环里每个交易日都产生一次查询（B2）。
"""

from __future__ import annotations

import logging
import threading
import time
from contextvars import ContextVar, Token

logger = logging.getLogger(__name__)

# 取消状态缓存 TTL（秒）：期间内重复调用不再查库
_CANCEL_CACHE_TTL_SECONDS = 5.0

_current_job_id: ContextVar[str | None] = ContextVar("current_job_id", default=None)
_cancel_cache: dict[str, tuple[float, bool]] = {}
_cache_lock = threading.Lock()


class JobCancelledError(Exception):
    """任务在安全检查点被协作取消。"""


def set_current_job(job_id: str | None) -> Token:
    """设置当前线程正在执行的任务 ID。

    Args:
        job_id: 任务标识，None 表示清除。

    Returns:
        ContextVar 令牌，用于执行结束后还原。
    """
    return _current_job_id.set(job_id)


def reset_current_job(token: Token) -> None:
    """还原任务上下文。

    Args:
        token: ``set_current_job`` 返回的令牌。
    """
    _current_job_id.reset(token)


def current_job_id() -> str | None:
    """返回当前线程正在执行的任务 ID，无任务上下文时返回 None。"""
    return _current_job_id.get()


def is_cancel_requested(job_id: str | None = None) -> bool:
    """查询任务是否已被请求取消（带短 TTL 缓存）。

    Args:
        job_id: 任务标识，缺省使用当前线程的任务上下文。

    Returns:
        True 表示已请求取消；无任务上下文或查询失败时返回 False。
    """
    target = job_id or _current_job_id.get()
    if not target:
        return False
    now = time.monotonic()
    with _cache_lock:
        cached = _cancel_cache.get(target)
        if cached is not None and now - cached[0] < _CANCEL_CACHE_TTL_SECONDS:
            return cached[1]
    requested = False
    try:
        from quant_etf_api.infra.job_queue.repository import JobRepository

        requested = JobRepository().is_cancel_requested(target)
    except Exception:
        # 取消是尽力而为的能力：查询失败不应打断正在执行的任务
        logger.debug("查询任务取消标记失败: job_id=%s", target, exc_info=True)
    with _cache_lock:
        _cancel_cache[target] = (now, requested)
    return requested


def ensure_not_cancelled() -> None:
    """安全检查点：已请求取消时抛出 ``JobCancelledError``。

    Raises:
        JobCancelledError: 任务已被请求取消。
    """
    if is_cancel_requested():
        raise JobCancelledError("任务已按请求取消")


def clear_cache(job_id: str | None = None) -> None:
    """清空取消状态缓存（任务结束时调用，避免缓存泄漏）。

    Args:
        job_id: 指定任务 ID；None 表示清空全部。
    """
    with _cache_lock:
        if job_id is None:
            _cancel_cache.clear()
        else:
            _cancel_cache.pop(job_id, None)


def cache_ttl_seconds() -> float:
    """返回取消状态缓存 TTL（秒），便于测试断言。"""
    return _CANCEL_CACHE_TTL_SECONDS
