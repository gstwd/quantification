"""独立后台任务 worker 进程入口（B1）。

回测是 CPU + 数据库混合型任务，在 API 进程内开线程并发既拿不到 GIL 收益，
又会把多份数据库负载叠加在一起。部署上推荐：

1. API 进程设置 ``QUANT_ETF_JOB_QUEUE_EMBEDDED=false``（只入队不消费）；
2. 单独启动本进程消费队列：

```bash
python -m quant_etf_api.worker
```

回测 lane 的并发由 ``QUANT_ETF_JOB_QUEUE_BACKTEST_WORKERS`` 控制（默认 1，
即串行），通用 lane 由 ``QUANT_ETF_JOB_QUEUE_WORKERS`` 控制。
"""

from __future__ import annotations

import logging
import signal
import threading
from types import FrameType

from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.job_queue.queue import get_job_queue

logger = logging.getLogger(__name__)

_shutdown = threading.Event()


def _handle_signal(signum: int, frame: FrameType | None) -> None:
    """收到终止信号时请求优雅退出。

    Args:
        signum: 信号编号。
        frame: 当前栈帧（未使用）。
    """
    logger.info("worker 收到信号 %s，准备停止", signum)
    _shutdown.set()


def main() -> None:
    """启动独立 worker 进程并阻塞至收到终止信号。"""
    setup_logging()
    settings = get_settings()
    queue = get_job_queue()
    try:
        recovered = queue.recover_stuck_jobs()
        if recovered:
            logger.warning("启动时恢复 %d 条卡死的 running 任务", recovered)
    except Exception:
        # 数据库未迁移等情况下不阻塞 worker 启动流程
        logger.warning("卡死任务恢复失败，继续启动 worker", exc_info=True)
    queue.start()
    logger.info(
        "独立 worker 已启动: general=%d backtest=%d 心跳=%ss 僵尸阈值=%ss 单任务上限=%ss",
        settings.job_queue_workers,
        settings.job_queue_backtest_workers,
        settings.job_heartbeat_interval_seconds,
        settings.job_stuck_timeout_seconds,
        settings.job_max_runtime_seconds,
    )
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        _shutdown.wait()
    finally:
        queue.stop()
        logger.info("独立 worker 已停止")


if __name__ == "__main__":
    main()
