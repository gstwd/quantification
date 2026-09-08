"""新增统一数据管理当前健康快照表。

Revision ID: 0037_data_management_health_snapshots
Revises: 0036_remove_factor_owner_plugin
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_data_management_health_snapshots"
down_revision = "0036_remove_factor_owner_plugin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建当前数据健康快照表。"""
    op.create_table(
        "data_health_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("dataset_key", sa.String(64), nullable=False, comment="静态数据集键"),
        sa.Column("partition_key", sa.String(64), nullable=False, server_default="", comment="分区键"),
        sa.Column("partition_name", sa.String(128), nullable=True, comment="分区展示名称"),
        sa.Column("health_status", sa.String(16), nullable=False, server_default="unknown", comment="健康状态"),
        sa.Column("source_name", sa.String(128), nullable=True, comment="最近实际来源"),
        sa.Column("earliest_date", sa.Date(), nullable=True, comment="库内最早业务日期"),
        sa.Column("latest_date", sa.Date(), nullable=True, comment="库内最新业务日期"),
        sa.Column("expected_date", sa.Date(), nullable=True, comment="目标日期"),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0", comment="记录数量"),
        sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0", comment="缺口数量"),
        sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0", comment="字段异常数量"),
        sa.Column("issue_summary", sa.JSON(), nullable=True, comment="当前问题摘要"),
        sa.Column("last_run_id", sa.String(64), nullable=True, comment="最近维护运行 ID"),
        sa.Column("last_run_status", sa.String(32), nullable=True, comment="最近维护运行状态"),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True, comment="最近检查时间（UTC）"),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True, comment="最近同步尝试时间（UTC）"),
        sa.Column("last_success_at", sa.DateTime(), nullable=True, comment="最近成功同步时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间（UTC）"),
        sa.UniqueConstraint("dataset_key", "partition_key", name="uq_data_health_snapshot_scope"),
    )
    op.create_index("ix_data_health_snapshot_dataset", "data_health_snapshot", ["dataset_key"])
    # 唯一索引均以交易日开头；质量检查按资产代码分组/筛选时需要反向索引。
    op.create_index("ix_index_daily_bar_code_date", "index_daily_bar", ["index_code", "trade_date"])
    op.create_index("ix_index_valuation_code_date", "index_valuation", ["index_code", "trade_date"])


def downgrade() -> None:
    """删除当前数据健康快照表。"""
    op.drop_index("ix_index_valuation_code_date", table_name="index_valuation")
    op.drop_index("ix_index_daily_bar_code_date", table_name="index_daily_bar")
    op.drop_index("ix_data_health_snapshot_dataset", table_name="data_health_snapshot")
    op.drop_table("data_health_snapshot")
