"""新增每日量化研究简报表。

Revision ID: 0068_market_daily_report
Revises: 0067_remove_ai_sentiment
Create Date: 2026-11-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0068_market_daily_report"
down_revision = "0067_remove_ai_sentiment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建日报表、日期索引与单日唯一发布约束。"""
    op.create_table(
        "market_daily_report",
        sa.Column(
            "report_id", sa.String(length=64), primary_key=True, comment="日报唯一 ID，UUID 格式"
        ),
        sa.Column("report_date", sa.Date(), nullable=False, comment="报告交易日"),
        sa.Column("revision_no", sa.Integer(), nullable=False, comment="同日修订号"),
        sa.Column(
            "status", sa.String(length=16), nullable=False, comment="published / superseded"
        ),
        sa.Column("title", sa.String(length=240), nullable=False, comment="日报标题"),
        sa.Column(
            "market_regime", sa.String(length=64), nullable=False, comment="当日市场状态标签"
        ),
        sa.Column(
            "readiness_status", sa.String(length=16), nullable=False, comment="ready / partial"
        ),
        sa.Column(
            "generator_version", sa.String(length=64), nullable=False, comment="生成器版本"
        ),
        sa.Column(
            "payload_version", sa.String(length=32), nullable=False, comment="报告内容契约版本"
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="内容生成时间（UTC）",
        ),
        sa.Column(
            "data_cutoff_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="数据截止时间（UTC）",
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="发布时间（UTC）",
        ),
        sa.Column(
            "content_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="完整日报内容",
        ),
        sa.Column(
            "fact_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="发布时绑定的确定性事实快照",
        ),
        sa.Column(
            "fact_snapshot_hash",
            sa.String(length=64),
            nullable=False,
            comment="事实快照 SHA-256",
        ),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=False,
            comment="完整内容 SHA-256",
        ),
        sa.UniqueConstraint(
            "report_date", "revision_no", name="uq_market_daily_report_revision"
        ),
        comment="每日量化研究简报不可变修订",
    )
    op.create_index(
        "ix_market_daily_report_date", "market_daily_report", ["report_date"], unique=False
    )
    op.create_index(
        "uq_market_daily_report_published",
        "market_daily_report",
        ["report_date"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )


def downgrade() -> None:
    """删除每日量化研究简报表。"""
    op.drop_index("uq_market_daily_report_published", table_name="market_daily_report")
    op.drop_index("ix_market_daily_report_date", table_name="market_daily_report")
    op.drop_table("market_daily_report")
