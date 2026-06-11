"""新增回测执行进度列：backtest_run.progress

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtest_run",
        sa.Column(
            "progress",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="回测执行进度（0-100），每完成约 10% 交易日更新一次",
        ),
    )


def downgrade() -> None:
    op.drop_column("backtest_run", "progress")
