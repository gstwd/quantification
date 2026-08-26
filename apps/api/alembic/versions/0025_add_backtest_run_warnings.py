"""backtest_run 增加 warnings 列（B6 配套：回测静默问题结构化透传）

Revision ID: 0025_add_backtest_run_warnings
Revises: 0024_backtest_signal_semantics
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0025_add_backtest_run_warnings"
down_revision = "0024_backtest_signal_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 warnings 列，存储回测执行过程中的结构化提示。"""
    op.add_column(
        "backtest_run",
        sa.Column(
            "warnings",
            JSONB(),
            nullable=True,
            comment="回测执行过程中的结构化提示（level/code/message/trade_date/index_code）",
        ),
    )


def downgrade() -> None:
    """删除 warnings 列。"""
    op.drop_column("backtest_run", "warnings")
