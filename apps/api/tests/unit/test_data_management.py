"""统一数据管理静态目录的基础测试。"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from quant_etf_api.infra.clients.index_daily_common import IndexDailyBar
from quant_etf_api.schemas.data_management import DataSetHealthSummary
from quant_etf_api.schemas.market_data import IndexValuation
import quant_etf_api.services.data_management_service as data_management_module
from quant_etf_api.services.data_management_service import (
    DATASETS,
    DataManagementService,
    _dataset_status,
)


def _definition(dataset_key: str):
    """按键返回静态数据集定义。"""
    return next(item for item in DATASETS if item.key == dataset_key)


def _inspect_payload(status: str = "healthy") -> dict:
    """构造最小健康载荷，用于纯逻辑测试。"""
    return {
        "": {
            "partition_name": None,
            "health_status": status,
            "source_name": "akshare",
            "earliest_date": date(2026, 1, 1),
            "latest_date": date(2026, 9, 8),
            "expected_date": date(2026, 9, 8),
            "record_count": 180,
            "missing_count": 0,
            "invalid_count": 0,
            "issue_summary": None,
        }
    }


def _make_service() -> DataManagementService:
    """构造注入 MagicMock 会话的服务实例。"""
    svc = DataManagementService(MagicMock())
    svc._db.query.return_value.filter.return_value.all.return_value = []
    return svc


def _capture_save_calls(svc: DataManagementService) -> list[tuple[str, dict]]:
    """将 _save_snapshot 替换为记录调用参数的空实现。"""
    calls: list[tuple[str, dict]] = []

    def fake_save(definition, partition_key, run_id, operation, payload, existing=None):
        """记录 key 与载荷。"""
        calls.append((partition_key, dict(payload)))
        return MagicMock(partition_key=partition_key)

    svc._save_snapshot = fake_save
    return calls


def test_all_non_news_external_datasets_are_registered() -> None:
    """验证首版非新闻外部数据集均在静态目录中注册。"""
    keys = {item.key for item in DATASETS}
    assert {
        "trading_calendar",
        "index_daily_bar",
        "index_valuation",
        "macro_indicator",
        "index_membership",
        "industry_universe",
        "industry_daily_bar",
        "industry_membership",
        "stock_universe",
        "stock_daily_close",
        "stock_daily_basic",
        "stock_moneyflow",
    } <= keys
    # research_run_item.index_code 已加宽到 64；此断言防止未来键超长再次触发截断
    assert all(len(item.key) <= 64 for item in DATASETS)


def test_dataset_operation_capabilities_match_data_semantics() -> None:
    """目录数据只支持检查/同步，其他数据集保留完整维护能力。"""
    for item in DATASETS:
        if item.key in {"industry_universe", "stock_universe"}:
            assert set(item.operations) == {"sync_latest", "check"}
        else:
            assert {"sync_latest", "check", "repair_gaps", "rebuild"} <= set(item.operations)


def test_warning_quality_rule_does_not_upgrade_to_error() -> None:
    """验证零量等告警不会再被汇总层误标为硬错误。"""
    service = DataManagementService(MagicMock())

    status = service._health_status(
        "index_daily_bar",
        "000300",
        100,
        date(2026, 9, 8),
        date(2026, 9, 8),
        0,
        0,
        2,
    )

    assert status == "warning"


def test_missing_count_reuses_preloaded_calendar_bounds() -> None:
    """验证缺口统计只依赖预加载日历边界，不需要加载逐日行情。"""
    service = DataManagementService(MagicMock())

    missing = service._missing_count_from_bounds(
        date(2026, 9, 1),
        3,
        [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)],
    )

    assert missing == 1


def test_stock_quality_uses_confirmed_source_coverage_not_ipo_boundary() -> None:
    """个股连续性只在已确认的接口覆盖区间内计算。"""
    earliest = date(2018, 1, 2)
    latest = date(2025, 12, 31)
    expected = date(2026, 9, 15)

    assert DataManagementService._coverage_bounds(
        "stock_moneyflow", earliest, latest, expected
    ) == (earliest, latest)
    assert DataManagementService._coverage_bounds(
        "index_daily_bar", earliest, latest, expected
    ) == (earliest, expected)


def test_upstream_missing_ranges_are_trade_day_compressed_and_expanded() -> None:
    """已确认上游空档按连续交易日压缩，并可按日历准确展开。"""
    calendar = [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 9),
    ]
    ranges = DataManagementService._compress_upstream_missing_ranges(
        {date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)},
        calendar,
        [],
        confirmed_at="2026-09-15T01:02:03Z",
    )

    assert [(item["start_date"], item["end_date"], item["trading_day_count"]) for item in ranges] == [
        ("2026-01-02", "2026-01-06", 3)
    ]
    assert DataManagementService._range_dates(ranges, calendar) == {
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
    }


def test_upstream_exemption_is_removed_when_local_date_is_no_longer_missing() -> None:
    """检查只保留仍未落库的豁免日期，补入本地数据后自动清理。"""
    service = DataManagementService(MagicMock())
    ranges = [
        {
            "start_date": "2026-01-02",
            "end_date": "2026-01-06",
            "trading_day_count": 3,
            "source": "tushare",
            "confirmed_at": "2026-09-15T01:02:03Z",
        }
    ]
    still_missing = [date(2026, 1, 2), date(2026, 1, 6)]

    assert service._upstream_exempt_dates(still_missing, ranges) == set(still_missing)
    cleaned = service._compress_upstream_missing_ranges(
        service._upstream_exempt_dates(still_missing, ranges),
        [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)],
        ranges,
    )
    assert [(item["start_date"], item["end_date"]) for item in cleaned] == [
        ("2026-01-02", "2026-01-02"),
        ("2026-01-06", "2026-01-06"),
    ]


def test_upstream_exemptions_keep_verification_sources_separate() -> None:
    """不同上游验证链的豁免日期不得被压缩为同一来源区间。"""
    service = DataManagementService(MagicMock())
    calendar = [date(2026, 1, 2), date(2026, 1, 5)]
    existing = [
        {
            "start_date": "2026-01-02",
            "end_date": "2026-01-02",
            "trading_day_count": 1,
            "source": "index_multi_source",
            "confirmed_at": "2026-09-15T01:02:03Z",
        },
        {
            "start_date": "2026-01-05",
            "end_date": "2026-01-05",
            "trading_day_count": 1,
            "source": "akshare_sw",
            "confirmed_at": "2026-09-15T01:02:03Z",
        },
    ]

    ranges = service._remaining_upstream_missing_ranges(set(calendar), calendar, existing)

    assert {(item["source"], item["start_date"], item["end_date"]) for item in ranges} == {
        ("index_multi_source", "2026-01-02", "2026-01-02"),
        ("akshare_sw", "2026-01-05", "2026-01-05"),
    }


def test_index_repair_writes_only_returned_targets_and_confirms_absent_dates() -> None:
    """指数修复只 upsert 目标日期；完整源链未返回的日期记录为上游空档。"""
    db = MagicMock()
    service = DataManagementService(db)
    first, second = date(2026, 1, 2), date(2026, 1, 5)
    service._partitions = lambda _: ["000300"]
    service._latest_trading_day = lambda **_kwargs: second
    service._calendar_days_until = lambda *_args, **_kwargs: [first, second]
    service._daily_gap_targets_for_repair = MagicMock(return_value={"000300": {first, second}})
    service._record_upstream_missing_dates_batch = MagicMock()
    bar = IndexDailyBar(
        trade_date=first,
        open_price=1.0,
        high_price=1.1,
        low_price=0.9,
        close_price=1.0,
        prev_close_price=1.0,
        change_pct=0.0,
        volume=1.0,
        turnover=1.0,
    )
    client = MagicMock(fetch_index_daily=MagicMock(return_value=[bar]))
    ingest = MagicMock(_build_index_daily_sources=MagicMock(return_value=[("akshare", client)]))
    ingest._insert_index_bars.return_value = 1

    result = service._repair_index_daily_gaps(None, ingest, force=False)

    assert result["records"] == 1
    assert result["upstream_missing_confirmed"] == 1
    ingest._insert_index_bars.assert_called_once()
    service._record_upstream_missing_dates_batch.assert_called_once_with(
        "index_daily_bar",
        {"000300": {second}},
        [first, second],
        source="index_multi_source",
    )


def test_industry_repair_does_not_confirm_when_request_fails() -> None:
    """申万请求失败不是上游空档证据，必须保留为待修复缺口。"""
    db = MagicMock()
    service = DataManagementService(db)
    trade_date = date(2026, 1, 2)
    service._partitions = lambda _: ["801010"]
    service._latest_trading_day = lambda **_kwargs: trade_date
    service._calendar_days_until = lambda *_args, **_kwargs: [trade_date]
    service._daily_gap_targets_for_repair = MagicMock(return_value={"801010": {trade_date}})
    service._record_upstream_missing_dates_batch = MagicMock()
    industry = MagicMock(repair_industry_dates=MagicMock(side_effect=RuntimeError("upstream down")))

    result = service._repair_industry_daily_gaps(None, industry, force=False)

    assert result["upstream_missing_confirmed"] == 0
    assert result["errors"]
    service._record_upstream_missing_dates_batch.assert_not_called()


def test_valuation_repair_uses_akshare_when_tushare_omits_target(monkeypatch) -> None:
    """首选 Tushare 缺日期时，AkShare 返回该日期应直接修复而非豁免。"""
    db = MagicMock()
    service = DataManagementService(db)
    trade_date = date(2026, 1, 2)
    service._partitions = lambda _: ["000300"]
    service._latest_trading_day = lambda **_kwargs: trade_date
    service._calendar_days_until = lambda *_args, **_kwargs: [trade_date]
    service._daily_gap_targets_for_repair = MagicMock(return_value={"000300": {trade_date}})
    service._record_upstream_missing_dates_batch = MagicMock()

    class FakeTushare:
        def is_configured(self) -> bool:
            return True

        def supports(self, _code: str) -> bool:
            return True

        def fetch_index_valuation(self, _code: str) -> list[IndexValuation]:
            return []

    class FakeAkShare:
        def fetch_index_valuation(self, _code: str) -> list[IndexValuation]:
            return [
                IndexValuation(
                    trade_date=trade_date,
                    index_code="000300",
                    pe=10.0,
                    pe_percentile=50.0,
                    pb=1.0,
                    pb_percentile=50.0,
                    dividend_yield=None,
                    source="akshare",
                )
            ]

    monkeypatch.setattr(data_management_module, "TushareIndexValuationClient", FakeTushare)
    monkeypatch.setattr(data_management_module, "AkShareIndexClient", FakeAkShare)
    ingest = MagicMock()
    ingest._insert_index_valuations.return_value = 1

    result = service._repair_index_valuation_gaps(None, ingest, force=False)

    assert result["records"] == 1
    assert result["upstream_missing_confirmed"] == 0
    ingest._insert_index_valuations.assert_called_once()
    service._record_upstream_missing_dates_batch.assert_not_called()


def test_stock_gap_repair_confirms_only_successful_tushare_negative_results() -> None:
    """成功全市场响应未包含目标证券才写上游豁免；失败响应不得写入。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [("000001",)]
    service = DataManagementService(db)
    service._partitions = lambda _: ["000001", "000002"]
    service._latest_trading_day = lambda **_kwargs: date(2026, 1, 5)
    service._calendar_days_until = lambda *_args, **_kwargs: [date(2026, 1, 5)]
    service._stock_gap_targets_for_repair = MagicMock(
        return_value={date(2026, 1, 5): {"000001", "000002"}}
    )
    service._record_upstream_missing_dates_batch = MagicMock()
    tushare = MagicMock(
        sync_trade_date=MagicMock(
            return_value={"records": {"stock_daily_close": 12}, "errors": []}
        )
    )

    result = service._repair_stock_daily_gaps("stock_daily_close", None, tushare, force=False)

    assert result["records"] == 12
    assert result["gaps_found"] == 2
    assert result["upstream_missing_confirmed"] == 1
    service._record_upstream_missing_dates_batch.assert_called_once_with(
        "stock_daily_close",
        {"000002": {date(2026, 1, 5)}},
        [date(2026, 1, 5)],
    )

    service._record_upstream_missing_dates_batch.reset_mock()
    tushare.sync_trade_date.return_value = {
        "records": {"stock_daily_close": 0},
        "errors": ["stock_daily_close: rate limited"],
    }
    failed = service._repair_stock_daily_gaps("stock_daily_close", None, tushare, force=False)
    assert failed["upstream_missing_confirmed"] == 0
    service._record_upstream_missing_dates_batch.assert_not_called()


def test_non_partitioned_dataset_check_keeps_single_summary_row() -> None:
    """无分区数据集检查结果应作为汇总行保留，不再被空聚合覆盖为 unknown。"""
    svc = _make_service()
    svc._inspect_many = lambda definition, partitions: _inspect_payload()
    calls = _capture_save_calls(svc)

    result = svc._check(_definition("trading_calendar"), None, "run-1", "check")

    assert [key for key, _ in calls] == [""]
    assert len(result) == 1
    assert calls[0][1]["health_status"] == "healthy"
    assert calls[0][1]["record_count"] == 180


def test_partition_only_check_does_not_refresh_aggregate_row() -> None:
    """单分区检查只应更新该分区，避免汇总行被标记为整集已检查。"""
    svc = _make_service()
    svc._inspect_many = lambda definition, partitions: {
        "000300": _inspect_payload()[""],
    }
    calls = _capture_save_calls(svc)

    svc._check(_definition("index_daily_bar"), "000300", "run-1", "check")

    assert [key for key, _ in calls] == ["000300"]


def test_daily_sync_skips_per_stock_exact_history_gap_queries() -> None:
    """日常同步避免为每个历史缺口股票执行日历反连接。"""
    svc = _make_service()
    inspect = MagicMock(return_value={})
    svc._inspect_many = inspect

    svc._check(_definition("stock_daily_close"), None, "run-1", "sync_latest")

    assert inspect.call_args.args == (_definition("stock_daily_close"), [])
    assert inspect.call_args.kwargs == {"exact_missing": False}


def test_dataset_status_derivation() -> None:
    """数据集状态应由分区错误与写入记录数共同决定。"""
    assert _dataset_status({"records": 0, "errors": []}) == "success"
    assert _dataset_status({"records": 3, "errors": ["000300: boom"]}) == "partial_success"
    assert _dataset_status({"records": 0, "errors": ["000300: boom"]}) == "failed"


def test_rebuild_replacement_span_guard() -> None:
    """重拉数据明显少于/未覆盖现有数据时应拒绝替换。"""
    svc = DataManagementService(MagicMock())
    existing_min = date(2020, 1, 2)
    existing_max = date(2026, 9, 8)
    fetched = {
        existing_min + timedelta(days=i)
        for i in range(0, (existing_max - existing_min).days + 1, 1)
    }
    svc._ensure_replacement_span("指数 000300", len(fetched), existing_min, existing_max, fetched)

    with pytest.raises(RuntimeError, match="明显少于库内现有数据"):
        svc._ensure_replacement_span(
            "指数 000300",
            len(fetched),
            existing_min,
            existing_max,
            set(list(fetched)[: len(fetched) // 2]),
        )
    with pytest.raises(RuntimeError, match="日期范围未覆盖"):
        svc._ensure_replacement_span(
            "指数 000300",
            len(fetched),
            existing_min,
            existing_max,
            fetched - {existing_min},
        )


def test_safe_replace_index_bars_uses_multi_source() -> None:
    """指数日线全量重拉必须走多源降级链，并把实际数据源写入库。

    回归点：重拉曾由 IngestService.rebuild_index_data 承载，现收敛到
    数据管理操作的 rebuild；不得退化为绕过降级链直连单一上游。
    """
    db = MagicMock()
    # 库内暂无该指数数据 -> 覆盖范围校验直接放行
    db.query.return_value.filter.return_value.one.return_value = (0, None, None)
    svc = DataManagementService(db)
    bars = [
        IndexDailyBar(
            trade_date=date(2026, 1, 5) + timedelta(days=i),
            open_price=100.0 + i,
            close_price=100.0 + i,
            high_price=101.0 + i,
            low_price=99.0 + i,
            volume=1000.0,
            turnover=1000000.0,
        )
        for i in range(3)
    ]
    ingest = MagicMock()
    ingest._fetch_index_daily_multi_source.return_value = (bars, "tickflow")

    assert svc._safe_replace_index_bars(ingest, "000300") == 3

    ingest._fetch_index_daily_multi_source.assert_called_once_with("000300")
    ingest._insert_index_bars.assert_called_once_with("000300", bars, source="tickflow")


def test_overview_reports_zero_snapshot_count_before_first_check() -> None:
    """尚无健康快照时总览应回报 snapshot_count=0，供前端给出友好提示。"""
    svc = DataManagementService(MagicMock())
    svc._db.query.return_value.scalar.return_value = 0

    def fake_summary(definition):
        """构造未检查数据集摘要。"""
        return DataSetHealthSummary(
            dataset_key=definition.key,
            display_name=definition.name,
            frequency=definition.frequency,
            source_label=definition.source_label,
            supported_operations=list(definition.operations),
            health_status="unknown",
        )

    svc._summary = fake_summary

    overview = svc.overview()

    assert overview.snapshot_count == 0
    assert overview.unknown_count == len(DATASETS)
    assert overview.healthy_count == 0
    # 五类状态计数必须覆盖全部数据集，避免出现"计数合计不等于数据集数"
    assert (
        overview.healthy_count
        + overview.warning_count
        + overview.error_count
        + overview.unknown_count
        + overview.unsupported_count
        == len(DATASETS)
    )


def test_overview_reports_existing_snapshot_count() -> None:
    """存在快照行时总览应回报真实行数，前端据此展示健康卡片。"""
    svc = DataManagementService(MagicMock())
    svc._db.query.return_value.scalar.return_value = 17
    svc._summary = lambda definition: DataSetHealthSummary(
        dataset_key=definition.key,
        display_name=definition.name,
        frequency=definition.frequency,
        source_label=definition.source_label,
        supported_operations=list(definition.operations),
        health_status="healthy",
    )

    overview = svc.overview()

    assert overview.snapshot_count == 17
    assert overview.healthy_count == len(DATASETS)
