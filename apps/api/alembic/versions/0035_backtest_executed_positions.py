"""backtest_daily_result 增加实际执行持仓字段。

Revision ID: 0035_backtest_executed_positions
Revises: 0034_remove_signal_mode_residue
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_backtest_executed_positions"
down_revision = "0034_remove_signal_mode_residue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增收益窗口实际执行持仓字段。"""
    op.add_column(
        "backtest_daily_result",
        sa.Column("executed_positions", sa.JSON(), nullable=True, comment="收益窗口实际执行持仓"),
    )


def downgrade() -> None:
    """删除收益窗口实际执行持仓字段。"""
    op.drop_column("backtest_daily_result", "executed_positions")
