"""后台任务处理器注册表。

所有后台任务类型对应的执行函数统一登记在 JOB_HANDLERS，
由 JobQueue worker 线程按 job_type 分发调用。
每个处理器自行创建独立数据库 Session，与请求 Session 隔离。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from quant_etf_api.infra.time import today_cn

logger = logging.getLogger(__name__)


def handle_daily_ingest(payload: dict) -> None:
    """执行日频数据摄取，落库完成后按实际行情日期入队因子计算。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.infra.job_queue.queue import get_job_queue
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        # 返回值仅在本次有新数据落库时非空：按实际补齐的交易日入队因子
        # 计算，避免按日历"今天"产生无行情日期的因子，也避免空跑重复计算
        data_date = IngestService(db).run_daily_ingest(run_id)
        if data_date is not None:
            get_job_queue().enqueue(
                "factor_computation",
                {"trade_date": data_date.isoformat()},
                job_key=f"factor_computation:{data_date.isoformat()}",
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
        StrategyExecutionService(db).execute(config, today_cn(), run_id, params)
    except Exception as e:
        logger.exception("策略执行任务异常: run_id=%s strategy_id=%s", run_id, strategy_id)
        RunService(db).mark_failed(run_id, f"策略执行异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_cold_start(payload: dict) -> None:
    """执行冷启动：拉取全部指数从成立至今的全量历史日线。"""
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


def handle_index_rebuild(payload: dict) -> None:
    """单指数全量覆盖重拉（删除旧历史数据后重新拉取全量）。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    index_code = payload.get("index_code") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).rebuild_index_data(run_id, index_code)
    except Exception as e:
        logger.exception("指数全量覆盖重拉任务异常: run_id=%s index_code=%s", run_id, index_code)
        RunService(db).mark_failed(run_id, f"指数全量覆盖重拉异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_index_incremental_fill(payload: dict) -> None:
    """单指数增量补数据（从数据库最新交易日补充到当天）。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.ingest_service import IngestService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    index_code = payload.get("index_code") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        IngestService(db).incremental_fill_index_data(run_id, index_code)
    except Exception as e:
        logger.exception("指数增量补数据任务异常: run_id=%s index_code=%s", run_id, index_code)
        RunService(db).mark_failed(run_id, f"指数增量补数据异常: {type(e).__name__}: {e}")
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
    """执行每日全局同步并驱动后续因子链路。

    同步完成后：行业日线/成分/个股收盘三项关键输入全部成功时按库内实际
    最新行业交易日入队行业面板因子；指数日线或估值有新记录时按实际最新
    指数交易日入队通用因子计算。
    """
    result = handle_data_management_operation({**payload, "operation": "sync_latest"})
    try:
        from sqlalchemy import func

        from quant_etf_api.infra.db.base import SessionLocal
        from quant_etf_api.infra.db.models.core import IndexDailyBarModel
        from quant_etf_api.infra.db.models.industry import IndustryDailyBarModel
        from quant_etf_api.infra.job_queue.queue import get_job_queue

        items = {item["dataset_key"]: item for item in result["items"]}
        critical_inputs = {"industry_daily_bar", "industry_membership", "stock_daily_close"}
        db = SessionLocal()
        try:
            industry_bar_records = int(items.get("industry_daily_bar", {}).get("records") or 0)
            industry_close_records = int(items.get("stock_daily_close", {}).get("records") or 0)
            if (
                all(items.get(key, {}).get("status") == "success" for key in critical_inputs)
                and (industry_bar_records > 0 or industry_close_records > 0)
            ):
                latest_industry = db.query(func.max(IndustryDailyBarModel.trade_date)).scalar()
                if latest_industry is not None:
                    get_job_queue().enqueue(
                        "industry_factor_compute",
                        {"trade_date": latest_industry.isoformat()},
                        job_key=f"industry_factor_compute:{latest_industry.isoformat()}",
                    )
            else:
                failed = [
                    key for key in critical_inputs
                    if items.get(key, {}).get("status") != "success"
                ]
                logger.warning("全局同步未满足行业因子前置数据（%s），跳过行业因子计算", ",".join(failed))

            bar_item = items.get("index_daily_bar", {})
            valuation_item = items.get("index_valuation", {})
            if (
                bar_item.get("status") == "success"
                and valuation_item.get("status") == "success"
                and (
                    int(bar_item.get("records") or 0) > 0
                    or int(valuation_item.get("records") or 0) > 0
                )
            ):
                data_date = db.query(func.max(IndexDailyBarModel.trade_date)).scalar()
                if data_date is not None:
                    get_job_queue().enqueue(
                        "factor_computation",
                        {"trade_date": data_date.isoformat()},
                        job_key=f"factor_computation:{data_date.isoformat()}",
                    )
        finally:
            db.close()
    except Exception:
        # 同步本身已成功并落库，后续因子入队失败不应把运行改写为失败
        logger.exception("全局同步完成，但后续因子任务入队失败")


def handle_factor_computation(payload: dict) -> None:
    """执行指定交易日的因子计算并入库。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.main import factor_registry  # noqa: PLC0415
    from quant_etf_api.services.run_service import RunService

    trade_date_str = payload.get("trade_date") or today_cn().isoformat()
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


def handle_industry_daily_ingest(payload: dict) -> None:
    """执行申万行业日频摄取，落库后按实际行情日入队行业因子计算。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.infra.job_queue.queue import get_job_queue
    from quant_etf_api.services.industry_data_service import IndustryDataService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        result = IndustryDataService(db).run_daily_ingest()
        bars = result.get("bars") or {}
        stock_snapshot = result.get("stock_snapshot") or {}
        target_date = result.get("target_date")
        bars_errors = bars.get("errors") or []
        snapshot_error = stock_snapshot.get("error")
        # 行情/快照不完整时不入队当日因子计算，避免用缺边缺角输入算扩散/RRG
        if (
            target_date is not None
            and not bars_errors
            and snapshot_error is None
        ):
            get_job_queue().enqueue(
                "industry_factor_compute",
                {"trade_date": target_date.isoformat()},
                job_key=f"industry_factor_compute:{target_date.isoformat()}",
            )
        else:
            result["factor_skipped"] = {
                "target_date": target_date.isoformat() if target_date else None,
                "reason": "行业日线或个股收盘快照存在未补齐错误，等待补齐后重跑",
                "bars_errors": len(bars_errors),
                "stock_snapshot_error": bool(snapshot_error),
            }
        RunService(db).mark_success(run_id, metrics=_json_safe(result))
        # 行业凌晨补拉会补齐前一日缺口；结束后入队全局质量检查，
        # 让统一健康快照在早上即反映补拉后的真实状态（幂等去重）。
        try:
            check_db = SessionLocal()
            try:
                check_run = RunService(check_db).create_run(
                    "data_manage_operation", None, today_cn(), params={"operation": "check"}
                )
                _, check_created = get_job_queue().enqueue_with_status(
                    "data_manage_operation",
                    {"run_id": check_run.run_id, "operation": "check"},
                    job_key="data_manage:check:all:all",
                )
                if not check_created:
                    RunService(check_db).mark_skipped(
                        check_run.run_id,
                        {"reason": "已有健康检查任务正在执行"},
                    )
            finally:
                check_db.close()
        except Exception:
            logger.warning("行业日频摄取后健康检查入队失败", exc_info=True)
    except Exception as e:
        logger.exception("行业日频摄取任务异常: run_id=%s", run_id)
        db.rollback()
        RunService(db).mark_failed(run_id, f"行业日频摄取异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_industry_factor_compute(payload: dict) -> None:
    """计算指定交易日申万行业因子并入库，并在默认口径完成后触发复合因子重算。"""
    from datetime import date as date_cls

    from quant_etf_api.domain.industry.constants import industry_params_hash
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.infra.job_queue.queue import get_job_queue
    from quant_etf_api.services.industry_factor_service import IndustryFactorService
    from quant_etf_api.services.run_service import RunService

    trade_date_str = payload.get("trade_date") or date_cls.today().isoformat()
    trade_date = date_cls.fromisoformat(trade_date_str)
    # 参数覆盖：研究页/CLI 以非默认参数触发行业面板计算时，payload 携带参数，
    # 落库带参数指纹，job_key 也带指纹防止默认/自定义任务互相去重
    lookback_ratio = int(payload.get("lookback_ratio", 220))
    lookback_mom = int(payload.get("lookback_mom", 60))
    smooth_window = int(payload.get("smooth_window", 20))
    diffusion_lookback = int(payload.get("diffusion_lookback", 220))
    benchmark_exclude = payload.get("benchmark_exclude")
    if benchmark_exclude is not None:
        benchmark_exclude = [str(c) for c in benchmark_exclude]
    db = SessionLocal()
    run_id = ""
    try:
        run_svc = RunService(db)
        run = run_svc.create_run("industry_factor_compute", None, trade_date)
        run_id = run.run_id
        run_svc.mark_running(run_id)
        metrics = IndustryFactorService(db).compute_and_store(
            start=trade_date,
            end=trade_date,
            lookback_ratio=lookback_ratio,
            lookback_mom=lookback_mom,
            smooth_window=smooth_window,
            diffusion_lookback=diffusion_lookback,
            benchmark_exclude=benchmark_exclude,
        )
        factor_row_count = int(metrics.get("factor_row_count") or 0)
        if factor_row_count <= 0:
            raise RuntimeError(
                "行业因子计算未写入任何行；请检查行业面板日期、日线覆盖与 warm-up 数据"
            )
        run_svc.mark_success(run_id, metrics=metrics)

        # RRG 指数匹配度只消费默认参数指纹的行业因子。行业面板成功落库后，
        # 重新计算同日指数复合因子，避免两个任务并发时先读到旧面板而留下空值。
        task_params_hash = industry_params_hash(
            lookback_ratio=lookback_ratio,
            lookback_mom=lookback_mom,
            smooth_window=smooth_window,
            diffusion_lookback=diffusion_lookback,
            benchmark_exclude=benchmark_exclude,
        )
        if task_params_hash == industry_params_hash():
            get_job_queue().enqueue(
                "factor_computation",
                {"trade_date": trade_date.isoformat()},
                job_key=f"factor_computation:{trade_date.isoformat()}",
            )
    except Exception as e:
        logger.exception("行业因子计算任务异常: trade_date=%s", trade_date)
        if run_id:
            db.rollback()
            RunService(db).mark_failed(run_id, f"行业因子计算异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def _run_stock_task(
    *,
    job_type: str,
    payload: dict,
    action: str,
) -> None:
    """执行单只股票后台任务（质量检查/补全/重拉）的公共骨架。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.run_service import RunService
    from quant_etf_api.services.stock_data_service import StockDataService

    run_id = payload.get("run_id") or ""
    stock_code = payload.get("stock_code") or ""
    db = SessionLocal()
    try:
        run_svc = RunService(db)
        run_svc.mark_running(run_id)
        service = StockDataService(db)
        if action == "quality":
            metrics = service.quality_check(stock_code)
        elif action == "fill":
            metrics = service.fill_stock(stock_code)
        elif action == "rebuild":
            metrics = service.rebuild_stock(stock_code)
        else:
            raise ValueError(f"未知单股任务 action: {action}")
        run_svc.mark_success(run_id, metrics=_json_safe(metrics))
    except Exception as e:
        logger.exception(
            "单股任务异常: job_type=%s run_id=%s stock_code=%s",
            job_type,
            run_id,
            stock_code,
        )
        db.rollback()
        RunService(db).mark_failed(run_id, f"单股任务失败: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def _json_safe(value: object) -> object:
    """递归把 dict/list 中的 date/datetime 转为 ISO 字符串（JSON 列入库用）。"""
    from datetime import date

    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def handle_stock_quality_check(payload: dict) -> None:
    """执行单只股票数据质量检查。"""
    _run_stock_task(job_type="stock_quality_check", payload=payload, action="quality")


def handle_stock_data_fill(payload: dict) -> None:
    """执行单只股票日线补全。"""
    _run_stock_task(job_type="stock_data_fill", payload=payload, action="fill")


def handle_stock_data_rebuild(payload: dict) -> None:
    """执行单只股票全量重拉。"""
    _run_stock_task(job_type="stock_data_rebuild", payload=payload, action="rebuild")


def _run_industry_task(
    *,
    job_type: str,
    payload: dict,
    action: str,
) -> None:
    """执行单个行业后台任务（质量检查/补全/重拉）的公共骨架。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.industry_data_service import IndustryDataService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    industry_code = payload.get("industry_code") or ""
    db = SessionLocal()
    try:
        run_svc = RunService(db)
        run_svc.mark_running(run_id)
        service = IndustryDataService(db)
        if action == "quality":
            metrics = service.quality_check(industry_code)
        elif action == "fill":
            metrics = service.fill_industry(industry_code)
        elif action == "rebuild":
            metrics = service.rebuild_industry(industry_code)
        else:
            raise ValueError(f"未知单行业任务 action: {action}")
        run_svc.mark_success(run_id, metrics=_json_safe(metrics))
    except Exception as e:
        logger.exception(
            "单行业任务异常: job_type=%s run_id=%s industry_code=%s",
            job_type,
            run_id,
            industry_code,
        )
        db.rollback()
        RunService(db).mark_failed(run_id, f"单行业任务失败: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_industry_universe_refresh(payload: dict) -> None:
    """执行行业目录与成分事件强制刷新（页面“更新行业信息”入口）。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.industry_data_service import IndustryDataService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        run_svc = RunService(db)
        run_svc.mark_running(run_id)
        service = IndustryDataService(db)
        universe = service.sync_universe()
        membership = service.refresh_membership(force=True)
        run_svc.mark_success(
            run_id,
            metrics={
                "universe": universe,
                "membership": membership,
            },
        )
    except Exception as e:
        logger.exception("行业目录刷新任务异常: run_id=%s", run_id)
        db.rollback()
        RunService(db).mark_failed(run_id, f"行业目录刷新异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_industry_bars_refresh(payload: dict) -> None:
    """执行全部行业日线增量刷新并重算质量快照。"""
    from quant_etf_api.infra.db.base import SessionLocal
    from quant_etf_api.services.industry_data_service import IndustryDataService
    from quant_etf_api.services.run_service import RunService

    run_id = payload.get("run_id") or ""
    db = SessionLocal()
    try:
        run_svc = RunService(db)
        run_svc.mark_running(run_id)
        service = IndustryDataService(db)
        bars = service.refresh_industry_bars_incremental()
        quality = service.bulk_quality()
        run_svc.mark_success(
            run_id,
            metrics=_json_safe({"bars": bars, "quality": quality}),
        )
    except Exception as e:
        logger.exception("行业日线刷新任务异常: run_id=%s", run_id)
        db.rollback()
        RunService(db).mark_failed(run_id, f"行业日线刷新异常: {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


def handle_industry_quality_check(payload: dict) -> None:
    """执行单个行业数据质量检查。"""
    _run_industry_task(job_type="industry_quality_check", payload=payload, action="quality")


def handle_industry_data_fill(payload: dict) -> None:
    """执行单个行业日线补全。"""
    _run_industry_task(job_type="industry_data_fill", payload=payload, action="fill")


def handle_industry_data_rebuild(payload: dict) -> None:
    """执行单个行业全量重拉。"""
    _run_industry_task(job_type="industry_data_rebuild", payload=payload, action="rebuild")


JOB_HANDLERS: dict[str, Callable[[dict], None]] = {
    "daily_ingest": handle_daily_ingest,
    "strategy_run": handle_strategy_run,
    "cold_start": handle_cold_start,
    "index_refresh": handle_index_refresh,
    "macro_refresh": handle_macro_refresh,
    "index_rebuild": handle_index_rebuild,
    "index_incremental_fill": handle_index_incremental_fill,
    "ai_analysis": handle_ai_analysis,
    "backtest": handle_backtest,
    "comparison": handle_comparison,
    "data_fill": handle_data_fill,
    "data_manage_operation": handle_data_management_operation,
    "data_sync_all": handle_data_sync_all,
    "factor_computation": handle_factor_computation,
    "warm_calendar": handle_warm_calendar,
    "industry_daily_ingest": handle_industry_daily_ingest,
    "industry_factor_compute": handle_industry_factor_compute,
    "industry_universe_refresh": handle_industry_universe_refresh,
    "industry_bars_refresh": handle_industry_bars_refresh,
    "industry_quality_check": handle_industry_quality_check,
    "industry_data_fill": handle_industry_data_fill,
    "industry_data_rebuild": handle_industry_data_rebuild,
    "stock_quality_check": handle_stock_quality_check,
    "stock_data_fill": handle_stock_data_fill,
    "stock_data_rebuild": handle_stock_data_rebuild,
}
