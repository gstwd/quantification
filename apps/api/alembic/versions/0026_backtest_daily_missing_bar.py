"""backtest_daily_result 增加 missing_bar_count 列（B10：数据缺口逐日可见）

Revision ID: 0026_backtest_daily_missing_bar
Revises: 0025_add_backtest_run_warnings
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_backtest_daily_missing_bar"
down_revision = "0025_add_backtest_run_warnings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 missing_bar_count 列，记录当日受数据缺口影响的持仓资产数。"""
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "missing_bar_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="当日受数据缺口影响的持仓资产数（0=无缺口）",
        ),
    )


def downgrade() -> None:
    """删除 missing_bar_count 列。"""
    op.drop_column("backtest_daily_result", "missing_bar_count")
