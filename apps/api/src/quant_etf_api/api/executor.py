"""后台任务线程池，供 runs、backtests 等路由共享使用。

统一的 Executor 确保所有后台任务使用同一线程池，
在进程退出时由 main.py lifespan 统一 shutdown。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

# 后台任务线程池，最大并发 3 个任务
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="bg-task")


def get_bg_executor() -> ThreadPoolExecutor:
    """返回共享后台任务线程池。"""
    return _executor
