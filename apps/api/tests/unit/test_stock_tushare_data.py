"""Tushare 个股数据服务、复权函数与写入门禁测试。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from quant_etf_api.domain.stocks.adjust import adjust_hfq, adjust_qfq
from quant_etf_api.infra.db.repositories.industry import StockDailyCloseRepository
from quant_etf_api.infra.db.repositories.stock_daily import (
    StockDailyBasicRepository,
    StockMoneyflowRepository,
)
from quant_etf_api.services.stock_data_service import StockDataService


def test_adjust_qfq_and_hfq_follow_pro_bar_formula() -> None:
    """复权价按 Tushare pro_bar 公式计算。"""
    assert adjust_qfq(10.0, 139.008, 139.008) == pytest.approx(10.0)
    assert adjust_qfq(10.0, 100.0, 200.0) == pytest.approx(5.0)
    assert adjust_hfq(10.0, 139.008) == pytest.approx(1390.08)
    assert adjust_qfq(None, 1.0, 1.0) is None
    assert adjust_hfq(10.0, None) is None


def test_normalize_datasets_aliases_and_errors() -> None:
    """数据集别名转换为静态键，未知别名抛错。"""
    service = StockDataService(MagicMock())
    assert service._normalize_datasets(["daily", "basic", "moneyflow"]) == [
        "stock_daily_close",
        "stock_daily_basic",
        "stock_moneyflow",
    ]
    assert service._normalize_datasets(None) == [
        "stock_daily_close",
        "stock_daily_basic",
        "stock_moneyflow",
    ]
    with pytest.raises(ValueError, match="未知个股数据集"):
        service._normalize_datasets(["unknown"])


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("600000", True),
        ("688001", True),
        ("000001", True),
        ("300750", True),
        ("430047", False),
        ("920000", False),
        ("900901", False),
    ],
)
def test_is_hs_stock_code(code: str, expected: bool) -> None:
    """沪深 A 股代码过滤仅保留 00/30/60/68 前缀。"""
    assert StockDataService._is_hs_stock_code(code) is expected


class _FakeTushareStockClient:
    """用于服务层同步测试的假 Tushare 客户端。"""

    def is_configured(self) -> bool:
        """始终返回已配置。"""
        return True

    def fetch_daily_by_trade_date(self, trade_date: date) -> list[dict]:
        """返回一条完整日线。"""
        return [
            {
                "trade_date": trade_date,
                "stock_code": "600000",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "pre_close": 10.0,
                "change": 0.2,
                "pct_chg": 2.0,
                "vol": 1000.0,
                "amount": 1020.0,
                "ah_vol": None,
                "ah_amount": None,
                "source": "tushare",
            }
        ]

    def fetch_adj_factor_by_trade_date(self, trade_date: date) -> list[dict]:
        """返回一条复权因子。"""
        return [
            {
                "trade_date": trade_date,
                "stock_code": "600000",
                "adj_factor": 139.008,
            }
        ]

    def fetch_daily_basic_by_trade_date(self, trade_date: date) -> list[dict]:
        """返回一条每日指标。"""
        return [
            {
                "trade_date": trade_date,
                "stock_code": "600000",
                "close": 10.2,
                "pe_ttm": 5.0,
                "limit_status": 1,
                "source": "tushare",
            }
        ]

    def fetch_moneyflow_by_trade_date(self, trade_date: date) -> list[dict]:
        """返回一条资金流向。"""
        return [
            {
                "trade_date": trade_date,
                "stock_code": "600000",
                "buy_sm_vol": 100,
                "buy_sm_amount": 12.5,
                "source": "tushare",
            }
        ]


def test_sync_universe_writes_only_tushare_securities() -> None:
    """证券基础信息同步不能由申万成分表补写占位证券。"""
    service = StockDataService(MagicMock())
    service._tushare_client = MagicMock()
    service._tushare_client.is_configured.return_value = True
    service._tushare_client.fetch_stock_basics.return_value = [
        {
            "stock_code": "600000",
            "ts_code": "600000.SH",
            "name_cn": "浦发银行",
            "market": "主板",
            "exchange": "SSE",
            "ipo_date": date(1999, 11, 10),
            "delist_date": None,
            "is_active": True,
            "source": "tushare",
        }
    ]
    service._universe_repo = MagicMock()
    service._universe_repo.find_all.return_value = []

    result = service.sync_universe()

    assert result == {"codes": 1, "added": 1, "updated": 1, "conflicts": 0}
    rows = service._universe_repo.bulk_upsert.call_args.args[0]
    assert rows == [
        {
            "stock_code": "600000",
            "name_cn": "浦发银行",
            "industry_code": None,
            "ts_code": "600000.SH",
            "market": "主板",
            "exchange": "SSE",
            "ipo_date": date(1999, 11, 10),
            "delist_date": None,
            "is_active": True,
            "source": "tushare",
            "created_at": rows[0]["created_at"],
            "updated_at": rows[0]["updated_at"],
        }
    ]


def test_single_stock_task_rejects_code_outside_tushare_universe() -> None:
    """单股补数不能借由申万成员代码创建非 Tushare 占位行。"""
    service = StockDataService(MagicMock())
    service._universe_repo = MagicMock()
    service._universe_repo.find_by_code.return_value = None

    with pytest.raises(ValueError, match="Tushare 沪深 A 股目录"):
        service._ensure_stock_row("920000")
    service._universe_repo.bulk_upsert.assert_not_called()


def test_sync_trade_date_merges_adj_factor_and_writes_three_tables() -> None:
    """按交易日同步会把复权因子并入日线，并分表写入。"""
    service = StockDataService(MagicMock())
    service._tushare_client = _FakeTushareStockClient()
    service._close_repo = MagicMock()
    service._close_repo.bulk_upsert.return_value = 1
    service._basic_repo = MagicMock()
    service._basic_repo.bulk_upsert.return_value = 1
    service._moneyflow_repo = MagicMock()
    service._moneyflow_repo.bulk_upsert.return_value = 1

    result = service.sync_trade_date(date(2026, 9, 8))

    assert result["records"] == {
        "stock_daily_close": 1,
        "stock_daily_basic": 1,
        "stock_moneyflow": 1,
    }
    close_rows = service._close_repo.bulk_upsert.call_args.args[0]
    assert close_rows[0]["adj_factor"] == 139.008
    assert close_rows[0]["source"] == "tushare"
    basic_rows = service._basic_repo.bulk_upsert.call_args.args[0]
    assert basic_rows[0]["limit_status"] == 1


def test_prune_before_dry_run_does_not_delete() -> None:
    """旧数据清理默认只预演。"""
    service = StockDataService(MagicMock())
    service._close_repo = MagicMock()
    service._close_repo.count_before.return_value = 42

    result = service.prune_before(date(2013, 1, 1), dry_run=True)

    assert result == {
        "before": date(2013, 1, 1),
        "matched": 42,
        "deleted": 0,
        "dry_run": True,
    }
    service._close_repo.delete_before_batch.assert_not_called()


def test_expected_bounds_floor_to_tushare_epoch() -> None:
    """质量与抓取区间下限统一为 2013-01-01，早退市股票返回空区间。"""
    service = StockDataService(MagicMock())
    service._latest_trading_day = lambda: date(2026, 9, 11)

    active = SimpleNamespace(ipo_date=date(1991, 4, 3), delist_date=None)
    start, end = service._expected_bounds(active, [])
    assert start == date(2013, 1, 1)
    assert end == date(2026, 9, 11)

    delisted = SimpleNamespace(ipo_date=date(1991, 1, 1), delist_date=date(2002, 6, 14))
    start, end = service._expected_bounds(delisted, [])
    assert start > end


def test_missing_trade_dates_compares_each_dataset() -> None:
    """缺口校验按数据集分别对比交易日历覆盖。"""
    service = StockDataService(MagicMock())
    days = [date(2026, 9, 8), date(2026, 9, 9)]
    service._trading_days = lambda start, end: days
    service._close_repo = MagicMock()
    service._close_repo.trading_dates_with_open.return_value = [days[0]]
    service._basic_repo = MagicMock()
    service._basic_repo.trading_dates.return_value = days
    service._moneyflow_repo = MagicMock()
    service._moneyflow_repo.trading_dates.return_value = []

    result = service.missing_trade_dates(days[0], days[1])

    assert result["stock_daily_close"] == [days[1]]
    assert result["stock_daily_basic"] == []
    assert result["stock_moneyflow"] == days


def _compiled_insert_statement(repo: object, rows: list[dict]) -> str:
    """执行仓库写入并把 SQL 编译为字符串，供 ON CONFLICT 断言。"""
    db = MagicMock()
    repository = repo(db)  # type: ignore[operator]
    repository.bulk_upsert(rows)
    statement = db.execute.call_args.args[0]
    return str(statement.compile(dialect=postgresql.dialect()))


def test_stock_close_upsert_updates_extension_fields() -> None:
    """stock_daily_close 冲突时更新 OHLC 等扩展字段。"""
    sql = _compiled_insert_statement(
        StockDailyCloseRepository,
        [
            {
                "trade_date": date(2026, 9, 8),
                "stock_code": "600000",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "adj_factor": 139.008,
                "source": "tushare",
            }
        ],
    )
    assert "DO UPDATE" in sql
    assert "adj_factor" in sql
    assert "IS DISTINCT FROM" in sql
    assert "ingested_at IS DISTINCT FROM" not in sql


def test_new_daily_tables_upsert_use_do_update() -> None:
    """每日指标与资金流向表冲突时更新字段。"""
    basic_sql = _compiled_insert_statement(
        StockDailyBasicRepository,
        [
            {
                "trade_date": date(2026, 9, 8),
                "stock_code": "600000",
                "close": 10.2,
                "limit_status": 1,
                "source": "tushare",
            }
        ],
    )
    moneyflow_sql = _compiled_insert_statement(
        StockMoneyflowRepository,
        [
            {
                "trade_date": date(2026, 9, 8),
                "stock_code": "600000",
                "buy_sm_vol": 100,
                "buy_sm_amount": 12.5,
                "source": "tushare",
            }
        ],
    )
    assert "DO UPDATE" in basic_sql
    assert "IS DISTINCT FROM" in basic_sql
    assert "ingested_at IS DISTINCT FROM" not in basic_sql
    assert "DO UPDATE" in moneyflow_sql
    assert "IS DISTINCT FROM" in moneyflow_sql
    assert "ingested_at IS DISTINCT FROM" not in moneyflow_sql
