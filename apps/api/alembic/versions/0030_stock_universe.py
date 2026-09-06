"""新增个股元数据表 stock_universe 与个股日线股票维度索引。

- stock_universe：个股目录与质量快照（对标 benchmark_index 对 index_daily_bar
  的关系；stock_daily_close 仅新增查询索引，不改动任何列）。
- ix_stock_daily_close_code_date：支撑按单只股票的质量检查/补全/重拉查询，
  避免 10M 行量级下对 stock_code 的单股查询退化为全表扫描。

Revision ID: 0030_stock_universe
Revises: 0029_industry_rrg_subsystem
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_stock_universe"
down_revision = "0029_industry_rrg_subsystem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建个股元数据表并补充个股日线股票维度索引。"""
    op.create_table(
        "stock_universe",
        sa.Column("stock_code", sa.String(16), primary_key=True),
        sa.Column("name_cn", sa.String(128), nullable=False, server_default=""),
        sa.Column(
            "industry_code",
            sa.String(16),
            sa.ForeignKey("industry_universe.industry_code"),
            nullable=True,
        ),
        sa.Column("ipo_date", sa.Date(), nullable=True),
        sa.Column("delist_date", sa.Date(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("data_start_date", sa.Date(), nullable=True),
        sa.Column("data_end_date", sa.Date(), nullable=True),
        sa.Column("bar_count", sa.Integer(), nullable=True),
        sa.Column("missing_day_count", sa.Integer(), nullable=True),
        sa.Column("quality_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_stock_universe_industry_code",
        "stock_universe",
        ["industry_code"],
    )
    op.create_index(
        "ix_stock_daily_close_code_date",
        "stock_daily_close",
        ["stock_code", "trade_date"],
    )


def downgrade() -> None:
    """删除个股元数据表与个股日线股票维度索引。"""
    op.drop_index("ix_stock_daily_close_code_date", table_name="stock_daily_close")
    op.drop_index("ix_stock_universe_industry_code", table_name="stock_universe")
    op.drop_table("stock_universe")
