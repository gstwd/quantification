"""行业数据源管理：industry_universe 质量快照列与行业日线代码索引。

- industry_universe 增加数据质量快照列（对标 stock_universe，由
  “数据质量检查/补全/重拉/日频摄取”刷新维护）；
- industry_daily_bar 增加 (industry_code, trade_date) 索引，支撑单行业
  质量检查、补全、重拉与详情 K 线查询。

Revision ID: 0031_industry_data_management
Revises: 0030_stock_universe
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_industry_data_management"
down_revision = "0030_stock_universe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为行业目录表增加质量快照列并补充行业日线代码维度索引。"""
    op.add_column(
        "industry_universe",
        sa.Column("data_start_date", sa.Date(), nullable=True, comment="库内日线最早日期（质量快照）"),
    )
    op.add_column(
        "industry_universe",
        sa.Column("data_end_date", sa.Date(), nullable=True, comment="库内日线最晚日期（质量快照）"),
    )
    op.add_column(
        "industry_universe",
        sa.Column("bar_count", sa.Integer(), nullable=True, comment="库内日线行数（质量快照）"),
    )
    op.add_column(
        "industry_universe",
        sa.Column(
            "missing_day_count",
            sa.Integer(),
            nullable=True,
            comment="按交易日历校验的缺失交易日数（质量快照）",
        ),
    )
    op.add_column(
        "industry_universe",
        sa.Column(
            "quality_checked_at",
            sa.DateTime(),
            nullable=True,
            comment="最近一次数据质量检查时间（UTC）",
        ),
    )
    op.create_index(
        "ix_industry_daily_bar_code_date",
        "industry_daily_bar",
        ["industry_code", "trade_date"],
    )


def downgrade() -> None:
    """删除行业目录质量快照列与行业日线代码维度索引。"""
    op.drop_index("ix_industry_daily_bar_code_date", table_name="industry_daily_bar")
    op.drop_column("industry_universe", "quality_checked_at")
    op.drop_column("industry_universe", "missing_day_count")
    op.drop_column("industry_universe", "bar_count")
    op.drop_column("industry_universe", "data_end_date")
    op.drop_column("industry_universe", "data_start_date")
