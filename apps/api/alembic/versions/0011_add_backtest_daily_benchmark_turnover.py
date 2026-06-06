"""添加 backtest_daily_result 表缺失列：benchmark_return、turnover

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "benchmark_return",
            sa.Float,
            nullable=True,
            comment="基准指数当日收益率，单位 %",
        ),
    )
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "turnover",
            sa.Float,
            nullable=True,
            comment="当日换手率，0-1",
        ),
    )


def downgrade() -> None:
    op.drop_column("backtest_daily_result", "turnover")
    op.drop_column("backtest_daily_result", "benchmark_return")
