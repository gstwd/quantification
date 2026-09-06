"""新增申万行业轮动子系统表（industry_* 与 stock_daily_close）。

表结构独立于 benchmark_index/index_daily_bar/index_factor_value：
- industry_universe：申万一级行业目录；
- industry_daily_bar：行业指数日线（无 benchmark_index 外键）；
- industry_membership_event：成分归属事件（start_date 生效）；
- stock_daily_close：个股收盘价（仅用于数量占比扩散）；
- industry_factor_value：行业因子值（rrg_rs_ratio/rrg_rs_momentum/
  rrg_quadrant/diffusion_count_ratio）。

Revision ID: 0029_industry_rrg_subsystem
Revises: 0028_backtest_snapshot_optimization
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_industry_rrg_subsystem"
down_revision = "0028_backtest_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建申万行业轮动子系统五张表。"""
    op.create_table(
        "industry_universe",
        sa.Column("industry_code", sa.String(16), primary_key=True),
        sa.Column("name_cn", sa.String(128), nullable=False),
        sa.Column(
            "classification",
            sa.String(16),
            nullable=False,
            server_default="sw",
        ),
        sa.Column(
            "is_benchmark_excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "industry_daily_bar",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "industry_code",
            sa.String(16),
            sa.ForeignKey("industry_universe.industry_code"),
            nullable=False,
        ),
        sa.Column("open_price", sa.Float(), nullable=True),
        sa.Column("high_price", sa.Float(), nullable=True),
        sa.Column("low_price", sa.Float(), nullable=True),
        sa.Column("close_price", sa.Float(), nullable=True),
        sa.Column("prev_close_price", sa.Float(), nullable=True),
        sa.Column("change_pct", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("turnover", sa.Float(), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("trade_date", "industry_code", name="uq_industry_daily_bar"),
    )

    op.create_table(
        "industry_membership_event",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("stock_code", sa.String(16), nullable=False),
        sa.Column(
            "industry_code",
            sa.String(16),
            sa.ForeignKey("industry_universe.industry_code"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "stock_code",
            "industry_code",
            "start_date",
            name="uq_industry_membership_event",
        ),
    )

    op.create_table(
        "stock_daily_close",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("stock_code", sa.String(16), nullable=False),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("trade_date", "stock_code", name="uq_stock_daily_close"),
    )

    op.create_table(
        "industry_factor_value",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "industry_code",
            sa.String(16),
            sa.ForeignKey("industry_universe.industry_code"),
            nullable=False,
        ),
        sa.Column("factor_id", sa.String(64), nullable=False),
        sa.Column("factor_value_numeric", sa.Float(), nullable=True),
        sa.Column("factor_payload", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "trade_date",
            "industry_code",
            "factor_id",
            name="uq_industry_factor_value",
        ),
    )
    op.create_index(
        "ix_industry_factor_value_date_factor",
        "industry_factor_value",
        ["trade_date", "factor_id"],
    )


def downgrade() -> None:
    """删除申万行业轮动子系统五张表。"""
    op.drop_index("ix_industry_factor_value_date_factor", table_name="industry_factor_value")
    op.drop_table("industry_factor_value")
    op.drop_table("stock_daily_close")
    op.drop_table("industry_membership_event")
    op.drop_table("industry_daily_bar")
    op.drop_table("industry_universe")
