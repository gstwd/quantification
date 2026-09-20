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
            db = SessionLocal()
            try:
                BacktestService(db).finalize_comparison_if_ready(comparison_id)
            finally:
                db.close()


def abandon_backtest(payload: dict, reason: str) -> None:
    """把被回收的异常回测任务对应回测标记为失败（B7）。

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
                from quant_etf_api.services.backtest_service import BacktestService

                BacktestService(db).finalize_comparison_if_ready(comparison_id)
            except Exception:
                logger.warning("对比回测收口失败: %s", comparison_id, exc_info=True)
    except Exception:
        logger.warning("异常回测任务清理失败: backtest_id=%s", backtest_id, exc_info=True)
    finally:
        db.close()


def abandon_comparison(payload: dict, reason: str) -> None:
    """把被回收的对比回测任务对应记录标记为失败（B7）。

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
    from quant_etf_api.services.backtest_service import BacktestService

    comparison_id = payload.get("comparison_id") or ""
    db = SessionLocal()
    try:
        children = BacktestService(db).launch_comparison(comparison_id)
    finally:
        db.close()

    if children is None:
        return
    backtest_a_id, backtest_b_id = children
    queue = get_job_queue()
    # 子回测沿用对比 ID 作为批次号，便于整批取消（B2）
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
    """执行 GET 查询未命中触发的后台补数任务。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.ingest_service import IngestService

    resource = payload.get("resource") or ""
    code = payload.get("code")
    db = SessionLocal()
    try:
        count = IngestService(db).fill_resource(resource, code)
        logger.info("后台补数完成: resource=%s code=%s records=%s", resource, code, count)
    except Exception:
        logger.exception("后台补数失败: resource=%s code=%s", resource, code)
        raise
    finally:
        db.close()


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
    """执行每日全局同步。

    因子值在实时分配与因子查询时按策略实际参数现算，不做批量预计算，
    因此同步完成后无需再驱动任何因子链路。
    """
    handle_data_management_operation({**payload, "operation": "sync_latest"})


def handle_warm_calendar(payload: dict) -> None:
    """预热交易日历缓存，避免首个请求触发慢速加载。

    严格口径（C1）：刷新后仍拿不到有效日历时抛错，让任务在 ``queue jobs``
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
    "warm_calendar": handle_warm_calendar,
}


# 异常任务回收回调（B7）：僵尸扫描把任务判失败时，同步把业务侧记录收口，
# 否则 backtest_run / backtest_comparison 会永远停在 running。
JOB_ABANDON_HANDLERS: dict[str, Callable[[dict, str], None]] = {
    "backtest": abandon_backtest,
    "comparison": abandon_comparison,
}
