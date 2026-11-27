from __future__ import annotations

import pytest

from quant_etf_api.postgres_mcp_server import (
    MAX_RESULT_ROWS,
    clamp_max_rows,
    normalize_database_url,
    validate_read_only_sql,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "  /* research */ SELECT 1",
        "-- comment\nWITH q AS (SELECT 1) SELECT * FROM q",
        "EXPLAIN SELECT 1",
        "SHOW statement_timeout",
        "VALUES (1), (2)",
        "TABLE backtest_run",
    ],
)
def test_validate_read_only_sql_accepts_queries(sql: str) -> None:
    """验证只读查询和白名单关键字可以通过。"""
    assert validate_read_only_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "INSERT INTO strategy_config DEFAULT VALUES",
        "UPDATE backtest_run SET status = 'failed'",
        "DELETE FROM research_trial",
        "DROP TABLE backtest_run",
        "SET transaction_read_only = off",
        "CALL maintenance_proc()",
    ],
)
def test_validate_read_only_sql_rejects_writes(sql: str) -> None:
    """验证写语句和非查询语句会被 MCP 提前拒绝。"""
    with pytest.raises(ValueError):
        validate_read_only_sql(sql)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-10, 1),
        (0, 1),
        (200, 200),
        (MAX_RESULT_ROWS + 1, MAX_RESULT_ROWS),
    ],
)
def test_clamp_max_rows(value: int, expected: int) -> None:
    """验证返回行数始终落在安全范围内。"""
    assert clamp_max_rows(value) == expected


def test_normalize_database_url_converts_psycopg_driver() -> None:
    """验证 SQLAlchemy 的 psycopg 驱动前缀会转换为 psycopg URL。"""
    assert (
        normalize_database_url("postgresql+psycopg://user:pass@db:5432/quant_etf")
        == "postgresql://user:pass@db:5432/quant_etf"
    )
