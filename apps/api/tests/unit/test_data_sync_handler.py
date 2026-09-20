"""全局数据同步处理器（data_sync_all）的任务入队测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from quant_etf_api.infra.job_queue import handlers


def _item(dataset_key: str, status: str = "success", records: int = 0) -> dict:
    """构造执行结果中的数据集条目。"""
    return {
        "dataset_key": dataset_key,
        "status": status,
        "records": records,
        "errors": [],
    }


def _patch_sync_env(monkeypatch, items: list[dict]) -> MagicMock:
    """替换数据管理执行与队列依赖。"""
    monkeypatch.setattr(
        handlers,
        "handle_data_management_operation",
        lambda payload: {"items": items, "status": "success"},
    )
    fake_queue = MagicMock()
    monkeypatch.setattr(
        "quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue
    )
    return fake_queue


def test_industry_failure_still_completes_index_sync(monkeypatch) -> None:
    """行业输入部分失败不影响指数同步完成，且同步不再派生任何因子任务。"""
    items = [
        _item("industry_daily_bar", status="partial_success", records=3),
        _item("industry_membership"),
        _item("stock_daily_close"),
        _item("index_daily_bar", records=5),
        _item("index_valuation"),
    ]
    fake_queue = _patch_sync_env(monkeypatch, items)

    handlers.handle_data_sync_all({})

    # 因子值改为按需现算，同步完成后不再入队任何因子计算任务
    fake_queue.enqueue.assert_not_called()


def test_no_new_records_does_not_requeue_any_factor(monkeypatch) -> None:
    """各数据集都无新增记录（重复同步）时不应入队任何因子计算。"""
    items = [
        _item("industry_daily_bar"),
        _item("industry_membership"),
        _item("stock_daily_close"),
        _item("index_daily_bar"),
        _item("index_valuation"),
    ]
    fake_queue = _patch_sync_env(monkeypatch, items)

    handlers.handle_data_sync_all({})

    job_types = [call.args[0] for call in fake_queue.enqueue.call_args_list]
    assert "factor_computation" not in job_types
    assert "industry_factor_compute" not in job_types


def test_data_management_partial_success_is_not_marked_failed(monkeypatch) -> None:
    """单数据集存在已完成工作和分区错误时，父运行应保留部分成功状态。"""
    from quant_etf_api.services import data_management_service
    from quant_etf_api.services import run_service

    calls: list[tuple[str, dict | None]] = []

    class FakeDataManagementService:
        def __init__(self, _db):
            pass

        def execute(self, *_args, **_kwargs):
            return {
                "status": "partial_success",
                "items": [
                    {
                        "dataset_key": "index_valuation",
                        "status": "partial_success",
                        "records": 5,
                        "errors": ["399673/akshare: upstream error"],
                    }
                ],
                "success_count": 0,
                "failed_count": 0,
                "partial_count": 1,
            }

    class FakeRunService:
        def __init__(self, _db):
            pass

        def mark_running(self, _run_id):
            return None

        def mark_success(self, _run_id, metrics=None):
            calls.append(("success", metrics))

        def mark_partial_success(self, _run_id, metrics=None):
            calls.append(("partial_success", metrics))

        def mark_failed(self, _run_id, _message):
            calls.append(("failed", None))

    monkeypatch.setattr(data_management_service, "DataManagementService", FakeDataManagementService)
    monkeypatch.setattr(run_service, "RunService", FakeRunService)
    monkeypatch.setattr("quant_etf_api.infra.db.base.SessionLocal", MagicMock())

    result = handlers.handle_data_management_operation(
        {"run_id": "run-1", "operation": "repair_gaps", "dataset_key": "index_valuation"}
    )

    assert result["status"] == "partial_success"
    assert calls == [("partial_success", result)]


def test_industry_daily_ingest_chain_removed() -> None:
    """行业日频摄取链已删除：行业数据只由全局同步按数据集增量补拉。"""
    from quant_etf_api.infra.job_queue.handlers import JOB_HANDLERS
    from quant_etf_api.services.industry_data_service import IndustryDataService

    assert "industry_daily_ingest" not in JOB_HANDLERS
    assert not hasattr(handlers, "handle_industry_daily_ingest")
    assert not hasattr(IndustryDataService, "run_daily_ingest")
    # 行业单对象与整批手动入口同样已下线（统一走数据管理操作）
    assert {
        "industry_universe_refresh",
        "industry_bars_refresh",
        "industry_quality_check",
        "industry_data_fill",
        "industry_data_rebuild",
    }.isdisjoint(JOB_HANDLERS)


def test_industry_stock_quality_helpers_removed() -> None:
    """行业/个股的平行质量实现已删除，质量只由数据管理服务写入。"""
    from quant_etf_api.services.industry_data_service import IndustryDataService
    from quant_etf_api.services.stock_data_service import StockDataService

    for service, method in (
        (IndustryDataService, "quality_check"),
        (IndustryDataService, "bulk_quality"),
        (IndustryDataService, "_persist_quality_snapshots"),
        (IndustryDataService, "industry_quality_detail"),
        (IndustryDataService, "backfill_industry_bars"),
        (StockDataService, "quality_check"),
        (StockDataService, "bulk_quality"),
        (StockDataService, "_persist_quality_snapshots"),
    ):
        assert not hasattr(service, method), f"{service.__name__}.{method} 应已删除"


def test_legacy_index_macro_ingest_entries_removed() -> None:
    """指数/宏观的旧摄取入口已下线：同步与全量重拉统一走数据管理操作。

    IngestService 只保留抓取、落库与读穿透；运行状态流转、进程内互斥锁与
    批量编排全部收敛到 DataManagementService，避免两套入口并发写同一批表。
    """
    from quant_etf_api.infra.job_queue.handlers import JOB_HANDLERS
    from quant_etf_api.services.ingest_service import IngestService

    legacy_job_types = {
        "daily_ingest",
        "cold_start",
        "index_refresh",
        "macro_refresh",
        "index_rebuild",
        "index_incremental_fill",
    }
    assert legacy_job_types.isdisjoint(JOB_HANDLERS)
    for method_name in (
        "run_daily_ingest",
        "run_cold_start",
        "refresh_index_data",
        "refresh_macro_data",
        "rebuild_index_data",
        "incremental_fill_index_data",
    ):
        assert not hasattr(IngestService, method_name)
    # GET 未命中触发的后台补数链路仍保留（与批量编排无关）
    assert "data_fill" in JOB_HANDLERS
    assert hasattr(IngestService, "fill_resource")
