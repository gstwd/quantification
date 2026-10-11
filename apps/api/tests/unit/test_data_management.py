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


def _bar(trade_date: date, **overrides: float | None) -> IndexDailyBar:
    """构造一根完整的指数日线，字段可按需覆盖。"""
    values: dict[str, float | None] = {
        "open_price": 100.0,
        "high_price": 101.0,
        "low_price": 99.0,
        "close_price": 100.0,
        "prev_close_price": 100.0,
        "change_pct": 0.0,
        "volume": 1000.0,
        "turnover": 100000.0,
    }
    values.update(overrides)
    return IndexDailyBar(trade_date=trade_date, **values)


def _make_service() -> DataManagementService:
    """构造注入 MagicMock 会话的服务实例。"""
    svc = DataManagementService(MagicMock())
    svc._db.query.return_value.filter.return_value.all.return_value = []
    return svc


def _capture_save_calls(svc: DataManagementService) -> list[tuple[str, dict]]:
    """将 _save_snapshot 替换为记录调用参数的空实现。"""
    calls: list[tuple[str, dict]] = []

    def fake_save(definition, partition_key, run_id, operation, payload, existing=None, **_kwargs):
        """记录 key 与载荷（运行状态等关键字参数忽略）。"""
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


def test_single_partial_dataset_keeps_operation_partial_success(monkeypatch) -> None:
    """不能因没有完全成功项而把单数据集部分成功降级成失败。"""
    service = DataManagementService(MagicMock())

    class FakeRunService:
        def __init__(self, _db):
            pass

        def add_item(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(data_management_module, "RunService", FakeRunService)
    service._execute_dataset = lambda *_args, **_kwargs: {
        "records": 5,
        "errors": ["399673/akshare: upstream error"],
    }

    result = service.execute("repair_gaps", "index_valuation", None, "run-1")

    assert result["success_count"] == 0
    assert result["partial_count"] == 1
    assert result["failed_count"] == 0
    assert result["status"] == "partial_success"


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
    ingest._drop_non_trading_bars.side_effect = lambda items: (items, [])
    ingest._insert_index_bars.return_value = 3

    assert svc._safe_replace_index_bars(ingest, "000300") == 3

    ingest._fetch_index_daily_multi_source.assert_called_once_with("000300")
    ingest._insert_index_bars.assert_called_once_with("000300", bars, source="tickflow")


def test_safe_replace_index_bars_drops_non_trading_days() -> None:
    """重建写回前必须剔除非交易日行，不能把基日/假日行情重新带回库内。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.one.return_value = (0, None, None)
    svc = DataManagementService(db)
    trading = _bar(date(2026, 1, 5))
    holiday = _bar(date(2026, 1, 1))
    ingest = MagicMock()
    ingest._fetch_index_daily_multi_source.return_value = ([holiday, trading], "tushare")
    ingest._drop_non_trading_bars.return_value = ([trading], [holiday])
    ingest._insert_index_bars.return_value = 1

    assert svc._safe_replace_index_bars(ingest, "000300") == 1

    ingest._insert_index_bars.assert_called_once_with("000300", [trading], source="tushare")


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


# ---------------------------------------------------------------------------
# 数据层修复回归（规则清单、涨跌幅分级、失败可见、下线分区、仅收盘价、日历重建）
# ---------------------------------------------------------------------------


def test_quality_rules_are_tuples_of_strings() -> None:
    """规则清单必须是字符串元组，单条规则漏写逗号会让接口返回逐字拆分的列表。"""
    for definition in DATASETS:
        assert isinstance(definition.quality_rules, tuple), definition.key
        assert all(isinstance(rule, str) and len(rule) > 1 for rule in definition.quality_rules)


@pytest.mark.parametrize(
    ("change_pct", "valid"),
    [(0.0, True), (17.88, True), (-18.5, True), (30.0, True), (30.5, False), (-100.0, False)],
)
def test_repair_validation_uses_graded_change_limit(change_pct: float, valid: bool) -> None:
    """修复校验以 30% 为错误线：2024 年科创50 +17.88% 等真实行情不能被拒收。"""
    item = _bar(date(2024, 9, 30), change_pct=change_pct)
    assert DataManagementService._valid_repair_item("index_daily_bar", item) is valid


def test_daily_bar_quality_conditions_grade_change_pct() -> None:
    """硬错误只认 >30% 或 -100%；15%~30% 进入告警；成交量为 0 不再产生告警。"""
    from sqlalchemy.dialects import postgresql

    svc = DataManagementService(MagicMock())
    hard, warning = svc._quality_conditions("index_daily_bar")
    options = {"literal_binds": True}
    hard_sql = str(hard.compile(dialect=postgresql.dialect(), compile_kwargs=options))
    warning_sql = str(warning.compile(dialect=postgresql.dialect(), compile_kwargs=options))
    assert "> 30" in hard_sql and "<= -99.99" in hard_sql
    assert "> 15" not in hard_sql
    assert "> 15" in warning_sql and "<= 30" in warning_sql
    assert "volume" not in warning_sql


def _capture_save_kwargs(svc: DataManagementService) -> dict[str, dict]:
    """把 _save_snapshot 替换为记录分区运行状态的空实现。"""
    calls: dict[str, dict] = {}

    def fake_save(definition, partition_key, run_id, operation, payload, existing=None, **kwargs):
        """记录分区键对应的载荷与运行状态参数。"""
        calls[partition_key] = {"payload": dict(payload), **kwargs}
        return MagicMock(partition_key=partition_key)

    svc._save_snapshot = fake_save
    return calls


def test_failed_partition_is_written_as_failed() -> None:
    """同步阶段单分区失败时，该分区快照必须记为 failed 并带上错误信息。"""
    svc = _make_service()
    svc._inspect_many = lambda definition, partitions, **_kwargs: {
        "000300": _inspect_payload()[""],
        "000903": _inspect_payload()[""],
    }
    svc._aggregate = MagicMock()
    calls = _capture_save_kwargs(svc)

    svc._check(
        _definition("index_valuation"),
        "000903",
        "run-1",
        "sync_latest",
        run_status="partial_success",
        run_errors=["000903: AttributeError: 'NoneType' object has no attribute 'attrs'"],
    )

    assert calls["000903"]["run_status"] == "failed"
    assert "AttributeError" in calls["000903"]["last_error"]
    assert calls["000300"]["run_status"] == "success"


def test_repair_error_prefix_with_source_maps_to_partition() -> None:
    """修复路径错误形如 "代码/来源: ..."，同样应定位到分区。"""
    svc = _make_service()
    svc._inspect_many = lambda definition, partitions, **_kwargs: {"399673": _inspect_payload()[""]}
    calls = _capture_save_kwargs(svc)

    svc._check(
        _definition("index_daily_bar"),
        "399673",
        "run-1",
        "repair_gaps",
        run_status="partial_success",
        run_errors=["399673/akshare: upstream error"],
    )

    assert calls["399673"]["run_status"] == "failed"


def test_save_snapshot_failure_keeps_last_success_time() -> None:
    """失败运行写入 last_error 与 failed，且不刷新 last_success_at。"""
    svc = DataManagementService(MagicMock())
    row = MagicMock(issue_summary=None, last_success_at="previous")
    svc._save_snapshot(
        _definition("index_valuation"),
        "000903",
        "run-1",
        "sync_latest",
        {"issue_summary": {"warning_count": 0}},
        row,
        run_status="failed",
        last_error="boom",
    )
    assert row.last_run_status == "failed"
    assert row.issue_summary["last_error"] == "boom"
    assert row.last_success_at == "previous"


def test_full_check_deletes_snapshots_of_inactive_partitions() -> None:
    """整集检查删除已不在可维护分区列表中的快照（退市证券、移除的指数）。"""
    db = MagicMock()
    stale = MagicMock(partition_key="931594", upstream_missing_ranges=[])
    active = MagicMock(partition_key="000300", upstream_missing_ranges=[])
    summary = MagicMock(partition_key="", upstream_missing_ranges=[])
    db.query.return_value.filter.return_value.all.return_value = [stale, active, summary]
    svc = DataManagementService(db)
    svc._partitions = lambda _key: ["000300"]
    svc._inspect_many = lambda definition, partitions, **_kwargs: {"000300": _inspect_payload()[""]}
    svc._aggregate = MagicMock()
    _capture_save_kwargs(svc)

    svc._check(_definition("index_daily_bar"), None, "run-1", "check")

    db.delete.assert_called_once_with(stale)


def test_close_only_ranges_are_kept_out_of_missing_logic_and_preserved() -> None:
    """仅收盘价区间单独传给检查器，并在写回时原样保留。"""
    close_only = {
        "start_date": "2016-01-04",
        "end_date": "2016-01-05",
        "trading_day_count": 2,
        "source": "close_only",
        "confirmed_at": "2026-10-11T00:00:00Z",
    }
    missing = {**close_only, "source": "index_multi_source"}
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        MagicMock(partition_key="931994", upstream_missing_ranges=[close_only, missing])
    ]
    svc = DataManagementService(db)
    seen: dict = {}

    def fake_inspect(definition, partitions, **kwargs):
        """记录检查器收到的区间并返回剩余缺失区间。"""
        seen.update(kwargs)
        return {"931994": {**_inspect_payload()[""], "upstream_missing_ranges": [missing]}}

    svc._inspect_many = fake_inspect
    calls = _capture_save_kwargs(svc)

    svc._check(_definition("index_daily_bar"), "931994", "run-1", "check", local_calendar=True)

    assert seen["upstream_missing_by_partition"] == {"931994": [missing]}
    assert seen["close_only_by_partition"] == {"931994": [close_only]}
    assert calls["931994"]["payload"]["upstream_missing_ranges"] == [missing, close_only]


def _close_only_repair_service(sources: list) -> tuple[DataManagementService, MagicMock, date]:
    """构造只有一个缺口目标的指数修复服务。"""
    service = DataManagementService(MagicMock())
    day = date(2016, 1, 4)
    service._partitions = lambda _: ["931994"]
    service._latest_trading_day = lambda **_kwargs: day
    service._calendar_days_until = lambda *_args, **_kwargs: [day]
    service._delete_non_trading_rows = MagicMock(return_value=0)
    service._daily_gap_targets_for_repair = MagicMock(return_value={"931994": {day}})
    service._record_upstream_missing_dates_batch = MagicMock()
    ingest = MagicMock(_build_index_daily_sources=MagicMock(return_value=sources))
    ingest._insert_index_bars.return_value = 1
    return service, ingest, day


def test_index_repair_records_close_only_dates() -> None:
    """所有来源都只给收盘价时写入收盘价，并登记为上游仅收盘价而非上游缺失。"""
    day = date(2016, 1, 4)
    close_only_bar = _bar(day, open_price=None, high_price=None, low_price=None)
    client = MagicMock(fetch_index_daily=MagicMock(return_value=[close_only_bar]))
    service, ingest, _ = _close_only_repair_service([("akshare", client)])

    result = service._repair_index_daily_gaps(None, ingest, force=False)

    assert result["close_only_confirmed"] == 1
    assert result["upstream_missing_confirmed"] == 0
    ingest._insert_index_bars.assert_called_once_with(
        "931994", [close_only_bar], source="akshare", overwrite=True
    )
    service._record_upstream_missing_dates_batch.assert_called_once_with(
        "index_daily_bar", {"931994": {day}}, [day], source="close_only"
    )


def test_index_repair_does_not_confirm_close_only_when_a_source_fails() -> None:
    """任一来源请求失败时，仅收盘价不是可信结论，不登记也不写入。"""
    day = date(2016, 1, 4)
    close_only_client = MagicMock(
        fetch_index_daily=MagicMock(
            return_value=[_bar(day, open_price=None, high_price=None, low_price=None)]
        )
    )
    failing = MagicMock(fetch_index_daily=MagicMock(side_effect=RuntimeError("timeout")))
    service, ingest, _ = _close_only_repair_service(
        [("akshare", close_only_client), ("tushare", failing)]
    )

    result = service._repair_index_daily_gaps(None, ingest, force=False)

    assert result["close_only_confirmed"] == 0
    ingest._insert_index_bars.assert_not_called()
    service._record_upstream_missing_dates_batch.assert_not_called()


def test_calendar_rebuild_replaces_wrong_dates_within_threshold(monkeypatch) -> None:
    """日历重建删除上游没有的本地日期、补入新增日期。"""
    monkeypatch.setattr(
        data_management_module, "validate_calendar_completeness", lambda _days: None
    )
    db = MagicMock()
    svc = DataManagementService(db)
    upstream = {date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)}
    existing = {date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 3)}

    # 2026-01-03 早于上游最早日期，不在覆盖范围内，不删除；只补 01-07
    assert svc._rebuild_calendar(upstream, existing) == 1
    db.query.return_value.filter.return_value.delete.assert_not_called()

    existing_with_wrong_day = existing | {date(2026, 1, 4)}
    db.reset_mock()
    upstream_wide = upstream | {date(2026, 1, 2)}
    # 删除 01-03、01-04（覆盖范围内上游没有），补入 01-02、01-07
    assert svc._rebuild_calendar(upstream_wide, existing_with_wrong_day) == 4
    db.query.return_value.filter.return_value.delete.assert_called_once()


def test_calendar_rebuild_aborts_when_too_many_removals(monkeypatch) -> None:
    """待删除日期超过阈值视为上游异常，中止并保留本地日历。"""
    monkeypatch.setattr(
        data_management_module, "validate_calendar_completeness", lambda _days: None
    )
    db = MagicMock()
    svc = DataManagementService(db)
    upstream = {date(2026, 1, 2), date(2026, 1, 30)}
    existing = {date(2026, 1, day) for day in range(5, 15)}

    with pytest.raises(RuntimeError, match="已中止重建"):
        svc._rebuild_calendar(upstream, existing)
    db.add.assert_not_called()
    db.query.return_value.filter.return_value.delete.assert_not_called()


def test_membership_repair_isolates_single_index_failure(monkeypatch) -> None:
    """成分修补逐指数隔离：一只失败不影响其余指数。"""
    calls: list[str] = []

    class FakeMembershipService:
        def __init__(self, _db) -> None:
            pass

        def backfill_pit(self, codes, _start, _end):
            """第一只指数抛错，其余正常返回。"""
            calls.append(codes[0])
            if codes[0] == "000300":
                raise RuntimeError("csindex down")
            return {"items": {codes[0]: 3}, "errors": []}

    monkeypatch.setattr(
        "quant_etf_api.services.index_membership_data_service.IndexMembershipDataService",
        FakeMembershipService,
    )
    svc = _make_service()
    svc._partitions = lambda _key: ["000300", "000905"]
    svc._latest_trading_day = lambda **_kwargs: date(2026, 9, 30)

    result = svc._sync("index_membership", None, "repair_gaps", False)

    assert calls == ["000300", "000905"]
    assert result["records"] == 3
    assert result["failed_partitions"] == ["000300"]


def test_partition_names_are_loaded_in_one_query() -> None:
    """分区名称按数据集一次批量查询，查不到时回退为代码本身。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [("000001", "平安银行")]
    svc = DataManagementService(db)

    names = svc._partition_names("stock_daily_close", ["", "000001", "000002"])

    assert names == {"": None, "000001": "平安银行", "000002": "000002"}
    assert db.query.call_count == 1


def test_execute_uses_thread_lock_outside_postgresql() -> None:
    """非 PostgreSQL 方言回退为进程内线程锁，不建立 advisory lock 连接。"""
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "sqlite"
    svc = DataManagementService(db)
    with svc._exclusive_operation():
        assert data_management_module._operation_lock.locked()
    assert not data_management_module._operation_lock.locked()
    db.get_bind.return_value.engine.connect.assert_not_called()


def test_nan_prices_are_not_valid_repair_items() -> None:
    """上游以 NaN 表示缺价时不能视为完整行；只有收盘价时应识别为仅收盘价。"""
    nan = float("nan")
    close_only = _bar(date(2016, 1, 4), open_price=nan, high_price=nan, low_price=nan)
    assert DataManagementService._valid_repair_item("index_daily_bar", close_only) is False
    assert DataManagementService._close_only_repair_item(close_only) is True
    nan_close = _bar(date(2016, 1, 4), close_price=nan)
    assert DataManagementService._close_only_repair_item(nan_close) is False
    nan_change = _bar(date(2016, 1, 4), change_pct=nan)
    assert DataManagementService._valid_repair_item("index_daily_bar", nan_change) is True
