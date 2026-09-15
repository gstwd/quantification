"""系统状态接口查询索引：为各数据表的入库时间列建索引（性能修复）。

背景：``GET /api/system/status`` 需要为每张数据表返回"记录数 / 最新业务日期 /
最近入库时间"。其中"最近入库时间"使用 ``max(ingested_at)`` 聚合，但
``ingested_at`` 上没有索引，PostgreSQL 只能全表扫描：

- ``stock_daily_close``（约 1250 万行）单条 ``max(ingested_at)`` 耗时约 14 秒；
- 加上 ``count(*)`` 的全表扫描，接口整体耗时约 15 秒。

本迁移为各表的入库时间列补建 btree 索引，使 ``max(ingested_at)`` 可以退化为
"索引反向扫描取首行"（O(1)）。行数统计的优化在服务层完成（大表改用
``pg_class.reltuples`` 统计估算，避免全表扫描）。

Revision ID: 0050_status_query_indexes
Revises: 0049_research_tooling
Create Date: 2026-09-15
"""

from __future__ import annotations

from alembic import op

revision = "0050_status_query_indexes"
down_revision = "0049_research_tooling"
branch_labels = None
depends_on = None

# (索引名, 表名, 列名)：系统状态接口需要求 max(入库时间) 的表
_INGEST_TIME_INDEXES: list[tuple[str, str, str]] = [
    ("ix_index_daily_bar_ingested_at", "index_daily_bar", "ingested_at"),
    ("ix_index_valuation_ingested_at", "index_valuation", "ingested_at"),
    ("ix_macro_indicator_ingested_at", "macro_indicator", "ingested_at"),
    ("ix_stock_daily_close_ingested_at", "stock_daily_close", "ingested_at"),
    ("ix_industry_daily_bar_ingested_at", "industry_daily_bar", "ingested_at"),
    ("ix_industry_membership_event_fetched_at", "industry_membership_event", "fetched_at"),
]


def upgrade() -> None:
    """为各数据表的入库时间列建立 btree 索引。"""
    for index_name, table_name, column_name in _INGEST_TIME_INDEXES:
        op.create_index(index_name, table_name, [column_name])


def downgrade() -> None:
    """删除入库时间索引。"""
    for index_name, table_name, _ in _INGEST_TIME_INDEXES:
        op.drop_index(index_name, table_name=table_name)
