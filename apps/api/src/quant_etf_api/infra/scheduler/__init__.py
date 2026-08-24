from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.infra.job_queue.queue import get_job_queue
from quant_etf_api.infra.trading_calendar import TradingCalendar
from quant_etf_api.services.run_service import RunService

logger = logging.getLogger(__name__)


class DailyIngestScheduler:
    """日频数据自动调度器。

    使用 daemon 线程 + Event 循环，在预定的时间点触发每日数据摄取。
    调度线程仅作为定时器：将摄取任务入队后立即返回，
    实际执行由后台任务队列的固定 worker 完成。
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
            from datetime import timedelta

            target = target + timedelta(days=1)
        return (target - now).total_seconds()

    def _execute_daily_ingest(self) -> None:
        """触发一次每日数据摄取任务（与手动触发走同一入队链路）。"""
        db = SessionLocal()
        try:
            today = date.today()
            if not TradingCalendar().is_trading_day(today):
                logger.info("调度器: 非交易日跳过 %s", today)
                return
            summary = RunService(db).create_run("daily_ingest", None, today)
            get_job_queue().enqueue(
                "daily_ingest",
                {"run_id": summary.run_id},
                job_key="daily_ingest",
            )
            logger.info("调度器: 日频入库任务已入队 run_id=%s", summary.run_id)
        except Exception:
            logger.exception("调度器: 日频入库任务入队失败")
        finally:
            db.close()


_scheduler: DailyIngestScheduler | None = None


def get_scheduler() -> DailyIngestScheduler:
    """获取全局调度器单例（数据摄取 + 因子计算）。

    首次调用时根据 settings.schedule_time 配置创建调度器实例。
    """
    global _scheduler
    if _scheduler is None:
        s = get_settings()
        parts = s.schedule_time.split(":")
        target = time(int(parts[0]), int(parts[1]))
        _scheduler = DailyIngestScheduler(target)
    return _scheduler


# ---------------------------------------------------------------------------
# AI 分析专用调度器
# ---------------------------------------------------------------------------


class AIAnalysisScheduler:
    """AI 舆情分析专用调度器。

    独立于数据摄取调度器，在每天 ai_schedule_time（默认 23:30）
    自动执行 AI 分析链路（新闻采集 → AI 情绪分析 → 聚合 → 市场研判）。
    使用独立线程 + Event 循环，与数据摄取调度器互不影响；
    调度线程仅将 ai_analysis 任务入队，实际执行在后台任务队列。
    """

    def __init__(self, target_time: time | None = None) -> None:
        """初始化 AI 分析调度器。

        Args:
            target_time: 触发时间，默认 23:30。
        """
        self._target_time = target_time or time(23, 30)
        self._shutdown_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动 AI 分析调度器后台线程。"""
        if self._thread is not None:
            return
        self._shutdown_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="ai-scheduler"
        )
        self._thread.start()
        logger.info("AI 分析调度器已启动，目标时间 %s", self._target_time.strftime("%H:%M"))

    def stop(self) -> None:
        """停止 AI 分析调度器。"""
        self._shutdown_event.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
            self._thread = None
        logger.info("AI 分析调度器已停止")

    def _loop(self) -> None:
        """调度主循环：等待目标时间，触发 AI 分析。"""
        while not self._shutdown_event.is_set():
            delay = self._seconds_until_target()
            if delay > 0:
                self._shutdown_event.wait(delay)
            if self._shutdown_event.is_set():
                break
            self._execute_ai_analysis()

    def _seconds_until_target(self) -> float:
        """计算距离下一次 AI 分析触发时间的秒数。"""
        now = datetime.now()
        target = now.replace(
            hour=self._target_time.hour,
            minute=self._target_time.minute,
            second=0,
            microsecond=0,
        )
        if target <= now:
            from datetime import timedelta

            target = target + timedelta(days=1)
        return (target - now).total_seconds()

    def _execute_ai_analysis(self) -> None:
        """触发一次 AI 舆情分析任务（入队后立即返回，独立于数据摄取链路）。"""
        db = SessionLocal()
        try:
            today = date.today()
            if not TradingCalendar().is_trading_day(today):
                logger.info("AI 调度器: 非交易日跳过 %s", today)
                return

            settings = get_settings()
            if not settings.ai_analysis_enabled:
                logger.info("AI 调度器: ai_analysis_enabled=False，跳过")
                return

            from quant_etf_api.infra.ai.client import AIClient  # noqa: PLC0415

            ai_client = AIClient.from_settings(settings)
            valid, err = ai_client.validate()
            if not valid:
                logger.warning("AI 调度器: AI 配置无效，跳过（%s）", err)
                return

            run_svc = RunService(db)
            ai_run = run_svc.create_run("ai_analysis", None, today)
            get_job_queue().enqueue(
                "ai_analysis",
                {"run_id": ai_run.run_id},
                job_key=f"ai_analysis:{today}",
            )
            logger.info("AI 调度器: AI 分析任务已入队 run_id=%s", ai_run.run_id)
        except Exception:
            logger.exception("AI 调度器: AI 分析任务入队失败")
        finally:
            db.close()


_ai_scheduler: AIAnalysisScheduler | None = None


def get_ai_scheduler() -> AIAnalysisScheduler:
    """获取 AI 分析调度器单例。

    首次调用时根据 settings.ai_schedule_time 配置创建调度器实例。
    """
    global _ai_scheduler
    if _ai_scheduler is None:
        s = get_settings()
        parts = s.ai_schedule_time.split(":")
        target = time(int(parts[0]), int(parts[1]))
        _ai_scheduler = AIAnalysisScheduler(target)
    return _ai_scheduler
