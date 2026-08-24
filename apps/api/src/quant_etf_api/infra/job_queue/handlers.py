"""后台任务处理器注册表。

所有后台任务类型对应的执行函数统一登记在 JOB_HANDLERS，
由 JobQueue worker 线程按 job_type 分发调用。
每个处理器自行创建独立数据库 Session，与请求 Session 隔离。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

logger = logging.getLogger(__name__)


def handle_daily_ingest(payload: dict) -> None:
    """执行日频数据摄取，落库完成后入队当日因子计算。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.infra.job_queue.queue import get_job_queue
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).run_daily_ingest(run_id)
        # 数据落库完成后触发当日因子计算，避免与摄取任务竞态
        today = date.today()
        get_job_queue().enqueue(
            "factor_computation",
            {"trade_date": today.isoformat()},
            job_key=f"factor_computation:{today}",
        )
    except Exception as e:
        logger.exception("数据摄取任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"数据摄取异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


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
        StrategyExecutionService(db).execute(config, date.today(), run_id, params)
    except Exception as e:
        logger.exception("策略执行任务异常: run_id=%s strategy_id=%s", run_id, strategy_id)
        RunService(db).mark_failed(run_id, f"策略执行异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_cold_start(payload: dict) -> None:
    """执行冷启动：拉取全部 ETF 和指数从成立至今的全量历史日线。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).run_cold_start(run_id)
    except Exception as e:
        logger.exception("冷启动任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"冷启动异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_startup_fill(payload: dict) -> None:
    """执行启动补全：仅补全有数据缺口的 ETF 和指数。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    db = SessionLocal()
    run_id = payload.get("run_id") or ""
    try:
        # 调度触发时无 run_id，由处理器自行创建运行记录；
        # 手动重试时复用传入的 run_id
        if not run_id:
            summary = RunService(db).create_run("startup_fill", None, date.today())
            run_id = summary.run_id
        RunService(db).mark_running(run_id)
        IngestService(db).run_startup_fill(run_id)
    except Exception as e:
        logger.exception("启动补全任务异常")
        if run_id:
            RunService(db).mark_failed(run_id, f"启动补全异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_universe_refresh(payload: dict) -> None:
    """执行 ETF 池元数据刷新。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.run_service import RunService
    from quant_etf_api.services.universe_service import UniverseService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        UniverseService(db).refresh_all(run_id)
    except Exception as e:
        logger.exception("ETF 池刷新任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"ETF 池刷新异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_etf_refresh(payload: dict) -> None:
    """刷新所有活跃 ETF 的日线和份额数据。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).refresh_etf_data(run_id)
    except Exception as e:
        logger.exception("ETF 数据刷新任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"ETF 数据刷新异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_index_refresh(payload: dict) -> None:
    """刷新指数日线和估值数据。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).refresh_index_data(run_id)
    except Exception as e:
        logger.exception("指数数据刷新任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"指数数据刷新异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_macro_refresh(payload: dict) -> None:
    """刷新宏观指标数据。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).refresh_macro_data(run_id)
    except Exception as e:
        logger.exception("宏观数据刷新任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(run_id, f"宏观数据刷新异常: {type(e).__name__}: {e}")
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
        stats = service.run_full_pipeline(target_date=date.today())
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
    queue.enqueue(
        "backtest",
        {"backtest_id": backtest_a_id, "comparison_id": comparison_id},
        job_key=f"comparison:{comparison_id}:a",
    )
    queue.enqueue(
        "backtest",
        {"backtest_id": backtest_b_id, "comparison_id": comparison_id},
        job_key=f"comparison:{comparison_id}:b",
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


def handle_factor_computation(payload: dict) -> None:
    """执行指定交易日的因子计算并入库。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.main import factor_registry  # noqa: PLC0415
    from quant_etf_api.services.run_service import RunService

    trade_date_str = payload.get("trade_date") or date.today().isoformat()
    trade_date = date.fromisoformat(trade_date_str)
    db = SessionLocal()
    run_id = ""
    try:
        from quant_etf_api.factors.service import FactorService

        run_svc = RunService(db)
        run = run_svc.create_run("factor_computation", None, trade_date)
        run_id = run.run_id
        run_svc.mark_running(run_id)
        result = FactorService(db, factor_registry).compute_and_store(trade_date)
        run_svc.mark_success(run_id, metrics=result)
    except Exception as e:
        logger.exception("因子计算任务异常: trade_date=%s", trade_date)
        if run_id:
            RunService(db).mark_failed(run_id, f"因子计算异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_warm_calendar(payload: dict) -> None:
    """预热交易日历缓存，避免首个请求触发慢速加载。"""
    from quant_etf_api.infra.trading_calendar import TradingCalendar

    TradingCalendar().refresh()


JOB_HANDLERS: dict[str, Callable[[dict], None]] = {
    "daily_ingest": handle_daily_ingest,
    "strategy_run": handle_strategy_run,
    "cold_start": handle_cold_start,
    "startup_fill": handle_startup_fill,
    "universe_refresh": handle_universe_refresh,
    "etf_refresh": handle_etf_refresh,
    "index_refresh": handle_index_refresh,
    "macro_refresh": handle_macro_refresh,
    "ai_analysis": handle_ai_analysis,
    "backtest": handle_backtest,
    "comparison": handle_comparison,
    "data_fill": handle_data_fill,
    "factor_computation": handle_factor_computation,
    "warm_calendar": handle_warm_calendar,
}
