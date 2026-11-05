"""后台任务处理器注册表。

所有后台任务类型对应的执行函数统一登记在 JOB_HANDLERS，
由 JobQueue worker 线程按 job_type 分发调用。
每个处理器自行创建独立数据库 Session，与请求 Session 隔离。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from quant_etf_api.infra.time import today_cn

logger = logging.getLogger(__name__)


def handle_strategy_run(payload: dict) -> None:
    """执行策略信号计算任务。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.run_service import RunService

    strategy_id = payload.get("strategy_id") or ""
    run_id = payload.get("run_id") or ""
    params = payload.get("params")
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        from quant_etf_api.services.strategy_config_service import StrategyConfigService
        from quant_etf_api.services.strategy_execution_service import StrategyExecutionService

        config_svc = StrategyConfigService(db)
        config = config_svc.get_parsed_config(strategy_id)
        if config is None:
            RunService(db).mark_failed(run_id, f"未找到策略配置: {strategy_id}")
            return
        StrategyExecutionService(db).execute(config, today_cn(), run_id, params)
    except Exception as e:
        logger.exception("策略执行任务异常: run_id=%s strategy_id=%s", run_id, strategy_id)
        RunService(db).mark_failed(run_id, f"策略执行异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_ai_analysis(payload: dict) -> None:
    """执行 AI 舆情分析完整链路（采集 → 分析 → 聚合 → 市场研判）。"""
    from quant_etf_api.ai_factors.service import AIFactorService
    from quant_etf_api.config.settings import get_settings
    from quant_etf_api.infra.ai.client import AIClient
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        settings = get_settings()
        client = AIClient.from_settings(settings)
        service = AIFactorService(db, client)
        stats = service.run_full_pipeline(target_date=today_cn())
        RunService(db).mark_success(run_id, metrics=stats)
    except Exception as e:
        logger.exception("AI 分析任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"AI 分析异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_backtest(payload: dict) -> None:
    """执行单个回测；若属于对比回测，完成后触发对比汇总。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.backtest_service import BacktestService

    backtest_id = payload.get("backtest_id") or ""
    comparison_id = payload.get("comparison_id")
    try:
        db = SessionLocal()
        try:
            BacktestService(db).run_backtest(backtest_id)
        finally:
            db.close()
    except Exception:
        logger.exception("回测任务异常: backtest_id=%s", backtest_id)
        raise
    finally:
        if comparison_id:
            from quant_etf_api.services.backtest_comparison_service import (
                BacktestComparisonService,
            )

            db = SessionLocal()
            try:
                BacktestComparisonService(db).finalize_comparison_if_ready(comparison_id)
            finally:
                db.close()


def abandon_backtest(payload: dict, reason: str) -> None:
    """把被回收的异常回测任务对应回测标记为失败。

    僵尸扫描只负责回收队列任务；若不同步回测记录，`backtest_run` 会
    永远停留在 running，批次汇总也就无法收口。

    Args:
        payload: 任务载荷（含 backtest_id / comparison_id）。
        reason: 回收原因。
    """
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.infra.db.repositories.backtest import BacktestRepository

    backtest_id = payload.get("backtest_id") or ""
    comparison_id = payload.get("comparison_id")
    if not backtest_id:
        return
    db = SessionLocal()
    try:
        repo = BacktestRepository(db)
        row = repo.find_by_id(backtest_id)
        if row is not None and row.status in ("pending", "running"):
            repo.mark_failed(
                backtest_id,
                f"任务被回收：{reason}",
                warnings=[
                    {
                        "level": "error",
                        "code": "JOB_ABANDONED",
                        "message": f"后台任务被回收：{reason}",
                    }
                ],
            )
        if comparison_id:
            try:
                from quant_etf_api.services.backtest_comparison_service import (
                    BacktestComparisonService,
                )

                BacktestComparisonService(db).finalize_comparison_if_ready(comparison_id)
            except Exception:
                logger.warning("对比回测收口失败: %s", comparison_id, exc_info=True)
    except Exception:
        logger.warning("异常回测任务清理失败: backtest_id=%s", backtest_id, exc_info=True)
    finally:
        db.close()


def abandon_comparison(payload: dict, reason: str) -> None:
    """把被回收的对比回测任务对应记录标记为失败。

    Args:
        payload: 任务载荷（含 comparison_id）。
        reason: 回收原因。
    """
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.infra.db.repositories.backtest import BacktestRepository

    comparison_id = payload.get("comparison_id") or ""
    if not comparison_id:
        return
    db = SessionLocal()
    try:
        repo = BacktestRepository(db)
        row = repo.find_comparison_by_id(comparison_id)
        if row is not None and row.status in ("pending", "running"):
            repo.mark_comparison_failed(comparison_id, f"任务被回收：{reason}")
    except Exception:
        logger.warning("异常对比任务清理失败: comparison_id=%s", comparison_id, exc_info=True)
    finally:
        db.close()


def handle_comparison(payload: dict) -> None:
    """启动对比回测：标记运行中并入队两个子回测任务，立即返回。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.infra.job_queue.queue import get_job_queue
    from quant_etf_api.services.backtest_comparison_service import BacktestComparisonService

    comparison_id = payload.get("comparison_id") or ""
    db = SessionLocal()
    try:
        children = BacktestComparisonService(db).launch_comparison(comparison_id)
    finally:
        db.close()

    if children is None:
        return
    backtest_a_id, backtest_b_id = children
    queue = get_job_queue()
    # 子回测沿用对比 ID 作为批次号，便于整批取消
    queue.enqueue(
        "backtest",
        {"backtest_id": backtest_a_id, "comparison_id": comparison_id},
        job_key=f"comparison:{comparison_id}:a",
        batch_id=comparison_id,
    )
    queue.enqueue(
        "backtest",
        {"backtest_id": backtest_b_id, "comparison_id": comparison_id},
        job_key=f"comparison:{comparison_id}:b",
        batch_id=comparison_id,
    )


def handle_data_fill(payload: dict) -> None:
    """已下线的 GET 读穿透补数任务（墓碑处理器）。

    外部数据只能经 ``POST /api/data-management/operations`` 统一入口写入，
    GET 查询未命中不再入队补数。保留该处理器只为消化升级前已排队的存量
    任务，避免其因"未知任务类型"反复失败；不执行任何抓取或写库。
    """
    logger.info(
        "忽略已下线的 data_fill 任务: resource=%s code=%s；请通过数据管理页同步",
        payload.get("resource"),
        payload.get("code"),
    )


def handle_data_management_operation(payload: dict) -> dict[str, Any]:
    """执行统一数据管理操作并按子数据集结果维护运行状态。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.data_management_service import DataManagementService
    from quant_etf_api.services.run_service import RunService

    run_id = str(payload.get("run_id") or "")
    db = SessionLocal()
    try:
        run_svc = RunService(db)
        run_svc.mark_running(run_id)
        result = DataManagementService(db).execute(
            str(payload.get("operation") or "check"),
            payload.get("dataset_key"),
            payload.get("partition_key"),
            run_id,
            force=bool(payload.get("force")),
        )
        if result["status"] == "success":
            run_svc.mark_success(run_id, metrics=result)
        elif result["status"] == "partial_success":
            run_svc.mark_partial_success(run_id, metrics=result)
        else:
            run_svc.mark_failed(run_id, "所有数据集维护操作均失败")
        return result
    except Exception as exc:
        logger.exception("统一数据管理任务异常: run_id=%s", run_id)
        db.rollback()
        RunService(db).mark_failed(run_id, f"数据管理异常: {type(exc).__name__}: {exc}")
        raise
    finally:
        db.close()


def handle_data_sync_all(payload: dict) -> None:
    """执行每日全局同步，之后记录同步失败告警、入队 LIVE 策略定时体检并做历史留存。

    因子值在实时分配与因子查询时按策略实际参数现算，不做批量预计算，
    因此同步完成后无需再驱动任何因子链路。体检只做诊断与站内告警，
    不改配置、不改生命周期状态；``lifecycle_auto_refresh_enabled=False`` 时不入队。
    历史留存只删除过期的已结束任务记录与已读告警，失败不影响同步结果。
    """
    from quant_etf_api.config.settings import get_settings
    from quant_etf_api.infra.job_queue.queue import get_job_queue
    from quant_etf_api.infra.time import today_cn

    result = handle_data_management_operation({**payload, "operation": "sync_latest"})
    today = today_cn().isoformat()
    _alert_sync_failures(result, today)
    if get_settings().lifecycle_auto_refresh_enabled:
        get_job_queue().enqueue(
            "lifecycle_refresh_all",
            {"trigger_date": today},
            job_key=f"lifecycle_refresh_all:{today}",
        )
    _prune_history()


def _prune_history() -> None:
    """删除超过留存天数的已结束任务记录与已读告警（异常只记日志）。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.retention_service import RetentionService

    db = SessionLocal()
    try:
        svc = RetentionService(db)
        jobs = svc.prune_job_history()
        alerts = svc.prune_read_alerts()
        logger.info("历史留存清理完成: 任务记录 %d 条，已读告警 %d 条", jobs, alerts)
    except Exception:  # noqa: BLE001 — 留存清理失败不能让同步失败
        logger.exception("历史留存清理失败")
        db.rollback()
    finally:
        db.close()


def _alert_sync_failures(result: dict[str, Any] | None, today: str) -> None:
    """每日同步有失败 / 部分失败的数据集时写一条站内告警（同一天只记一条）。

    Args:
        result: 数据管理操作结果（含 items）。
        today: 业务日期。
    """
    items = [
        item
        for item in (result or {}).get("items") or []
        if item.get("status") in ("failed", "partial_success")
    ]
    if not items:
        return
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.alert_service import KIND_DATA_SYNC_FAILED, AlertService

    db = SessionLocal()
    try:
        AlertService(db).raise_alert(
            kind=KIND_DATA_SYNC_FAILED,
            severity="warning",
            title=f"每日数据同步有 {len(items)} 个数据集失败或部分失败",
            detail={
                "datasets": [
                    {
                        "dataset_key": item.get("dataset_key"),
                        "status": item.get("status"),
                        "errors": (item.get("errors") or [])[:5],
                    }
                    for item in items
                ]
            },
            dedupe_key=f"{KIND_DATA_SYNC_FAILED}:{today}",
        )
    finally:
        db.close()


def handle_lifecycle_refresh_all(payload: dict) -> None:
    """刷新全部 LIVE 策略健康快照（定时体检），恶化或失败写站内告警。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.strategy_lifecycle_service import StrategyLifecycleService

    db = SessionLocal()
    try:
        summary = StrategyLifecycleService(db).refresh_all_live()
        logger.info("LIVE 策略定时体检完成: %s", summary)
    finally:
        db.close()


def handle_warm_calendar(payload: dict) -> None:
    """预热交易日历缓存，避免首个请求触发慢速加载。

    严格口径：刷新后仍拿不到有效日历时抛错，让任务在 ``queue jobs``
    中显式落为失败，而不是"预热成功但日历为空"。
    """
    from quant_etf_api.infra.trading_calendar import TradingCalendar

    calendar = TradingCalendar()
    calendar.refresh()
    # 刷新后仍拿不到日历时抛错（含修复指引），让队列记录失败原因
    calendar.require_trading_days()


JOB_HANDLERS: dict[str, Callable[[dict], None]] = {
    "strategy_run": handle_strategy_run,
    "ai_analysis": handle_ai_analysis,
    "backtest": handle_backtest,
    "comparison": handle_comparison,
    "data_fill": handle_data_fill,
    "data_manage_operation": handle_data_management_operation,
    "data_sync_all": handle_data_sync_all,
    "lifecycle_refresh_all": handle_lifecycle_refresh_all,
    "warm_calendar": handle_warm_calendar,
}


def abandon_run(payload: dict, reason: str) -> None:
    """把被回收的异常任务对应的运行记录（research_run）标记为失败。

    策略运行、AI 分析、全局同步与数据维护任务都带 run_id；任务被僵尸扫描回收后
    若不同步运行记录，运行记录页会永远显示执行中并持续轮询。已处于终态的记录不改写。

    Args:
        payload: 任务载荷（含 run_id）。
        reason: 回收原因。
    """
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.run_service import RunService

    run_id = str(payload.get("run_id") or "")
    if not run_id:
        return
    db = SessionLocal()
    try:
        svc = RunService(db)
        detail = svc.get_run_detail(run_id)
        if detail is not None and detail.status in ("pending", "running"):
            svc.mark_failed(run_id, f"任务被回收：{reason}")
    except Exception:
        logger.warning("异常运行任务清理失败: run_id=%s", run_id, exc_info=True)
    finally:
        db.close()


# 异常任务回收回调：僵尸扫描把任务判失败时，同步把业务侧记录收口，
# 否则 backtest_run / backtest_comparison / research_run 会永远停在 running。
JOB_ABANDON_HANDLERS: dict[str, Callable[[dict, str], None]] = {
    "backtest": abandon_backtest,
    "comparison": abandon_comparison,
    "strategy_run": abandon_run,
    "ai_analysis": abandon_run,
    "data_sync_all": abandon_run,
    "data_manage_operation": abandon_run,
}
