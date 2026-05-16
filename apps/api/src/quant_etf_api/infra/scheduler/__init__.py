from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.services.ingest_service import IngestService
from quant_etf_api.services.run_service import RunService

logger = logging.getLogger(__name__)


class DailyIngestScheduler:
    """日频数据自动调度器。

    使用 daemon 线程 + Event 循环，在预定的时间点触发每日数据摄取。
    任务在独立 Session 中执行，与请求 Session 完全隔离。
    """

    def __init__(self, target_time: time | None = None) -> None:
        self._target_time = target_time or time(17, 30)
        self._shutdown_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动调度器后台线程。"""
        if self._thread is not None:
            return
        self._shutdown_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        logger.info("调度器已启动，目标时间 %s", self._target_time.strftime("%H:%M"))

    def stop(self) -> None:
        """停止调度器，等待当前执行完成。"""
        self._shutdown_event.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
            self._thread = None
        logger.info("调度器已停止")

    def _loop(self) -> None:
        while not self._shutdown_event.is_set():
            delay = self._seconds_until_target()
            if delay > 0:
                self._shutdown_event.wait(delay)
            if self._shutdown_event.is_set():
                break
            self._execute_daily_ingest()

    def _seconds_until_target(self) -> float:
        """计算距离下一次触发时间的秒数。"""
        now = datetime.now()
        target = now.replace(
            hour=self._target_time.hour,
            minute=self._target_time.minute,
            second=0,
            microsecond=0,
        )
        if target <= now:
            target = target.replace(day=now.day + 1)
        return (target - now).total_seconds()

    def _execute_daily_ingest(self) -> None:
        """执行一次每日数据摄取（与手动触发走同一链路）。"""
        db = SessionLocal()
        try:
            today = date.today()
            if today.weekday() >= 5:
                logger.info("调度器: 周末跳过 %s", today)
                return
            summary = RunService(db).create_run("daily_ingest", None, today)
            IngestService(db).run_daily_ingest(summary.run_id)
            logger.info("调度器: 日频入库完成 run_id=%s", summary.run_id)
        except Exception:
            logger.exception("调度器: 日频入库失败")
        finally:
            db.close()


_scheduler: DailyIngestScheduler | None = None


def get_scheduler() -> DailyIngestScheduler:
    """获取全局调度器单例。

    首次调用时根据 settings 配置创建调度器实例。
    """
    global _scheduler
    if _scheduler is None:
        s = get_settings()
        parts = s.schedule_time.split(":")
        target = time(int(parts[0]), int(parts[1]))
        _scheduler = DailyIngestScheduler(target)
    return _scheduler
