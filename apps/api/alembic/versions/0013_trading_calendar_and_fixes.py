"""添加交易日历表 + benchmark_index 增加存活标记 + macro_indicator 增加标准化日期

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 交易日历表
    op.create_table(
        "trading_calendar",
        sa.Column("trade_date", sa.Date, primary_key=True, comment="日期"),
        sa.Column(
            "is_trading_day",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("TRUE"),
            comment="是否为交易日",
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("NOW()"),
            comment="记录创建时间（UTC）",
        ),
    )

    # 2. benchmark_index 增加存活标记
    op.add_column(
        "benchmark_index",
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("TRUE"),
            comment="是否活跃，False=已退市/停发",
        ),
    )
    op.add_column(
        "benchmark_index",
        sa.Column(
            "delisting_date",
            sa.Date,
            nullable=True,
            comment="退市/停发日期",
        ),
    )

    # 3. macro_indicator 增加标准化日期
    op.add_column(
        "macro_indicator",
        sa.Column(
            "period_date",
            sa.Date,
            nullable=True,
            comment="标准化周期日期，CPI/PMI 取当月首日，LPR 取报价日",
        ),
    )


def downgrade() -> None:
    op.drop_column("macro_indicator", "period_date")
    op.drop_column("benchmark_index", "delisting_date")
    op.drop_column("benchmark_index", "is_active")
    op.drop_table("trading_calendar")
