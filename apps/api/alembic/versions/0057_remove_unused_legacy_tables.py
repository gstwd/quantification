"""删除未接入运行时的遗留表。

Revision ID: 0057_remove_unused_legacy_tables
Revises: 0056_parameterized_factor_templates
Create Date: 2026-09-20

``source_payload_log`` 从未进入现行数据管理读写链路；外部数据原始响应
不再持久化。``signal_definition`` 也没有服务、路由或策略配置引用，实际
策略输出由 ``index_signal`` 与 ``research_run`` 表承载。两表均不存在被其
他表引用的外键，升级将永久删除其中的历史数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057_remove_unused_legacy_tables"
down_revision = "0056_parameterized_factor_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除不再被运行时使用的遗留表。"""
    op.drop_table("signal_definition")
    op.drop_table("source_payload_log")


def downgrade() -> None:
    """重建表结构；已删除的历史数据无法恢复。"""
    op.create_table(
        "source_payload_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_key", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("request_meta", sa.JSON(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "signal_definition",
        sa.Column("signal_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("signal_id"),
    )
