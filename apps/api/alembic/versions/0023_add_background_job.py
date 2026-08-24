"""add background_job table

Revision ID: 0023_background_job
Revises: 0022_keyword_tag_config
Create Date: 2026-08-24 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_background_job"
down_revision = "0022_keyword_tag_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 background_job 任务队列表及其索引。"""
    op.create_table(
        "background_job",
        sa.Column("job_id", sa.String(64), primary_key=True, comment="任务唯一 ID，UUID 格式"),
        sa.Column(
            "job_type",
            sa.String(64),
            nullable=False,
            comment="任务类型：daily_ingest/backtest/data_fill 等",
        ),
        sa.Column(
            "job_key",
            sa.String(256),
            nullable=True,
            comment="去重键，pending/running 状态下唯一",
        ),
        sa.Column("payload", sa.JSON(), nullable=True, comment="任务参数，JSON 格式"),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="任务状态：pending/running/success/failed",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="优先级（越大越先执行）",
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="已尝试次数",
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="最大尝试次数",
        ),
        sa.Column("error_message", sa.Text(), nullable=True, comment="失败时的错误信息"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            comment="创建时间（UTC）",
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="开始执行时间（UTC）"),
        sa.Column("finished_at", sa.DateTime(), nullable=True, comment="完成时间（UTC）"),
    )
    op.create_index(
        "ix_background_job_status_priority_created",
        "background_job",
        ["status", "priority", "created_at"],
    )
    op.create_index(
        "uq_background_job_active_key",
        "background_job",
        ["job_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    """删除 background_job 任务队列表。"""
    op.drop_index("uq_background_job_active_key", table_name="background_job")
    op.drop_index("ix_background_job_status_priority_created", table_name="background_job")
    op.drop_table("background_job")
