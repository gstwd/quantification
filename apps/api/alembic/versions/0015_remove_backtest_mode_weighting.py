"""移除回测信号评分模式：删除 backtest_mode 和 weighting 列

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("backtest_run", "backtest_mode")
    op.drop_column("backtest_run", "weighting")


def downgrade() -> None:
    op.add_column(
        "backtest_run",
        sa.Column(
            "backtest_mode",
            sa.String(32),
            nullable=False,
            server_default="signal",
            comment="回测模式：signal=信号评分，allocation=资产配置",
        ),
    )
    op.add_column(
        "backtest_run",
        sa.Column(
            "weighting",
            sa.String(32),
            nullable=False,
            server_default="equal",
            comment="组合加权方式：equal=等权，signal_weighted=信号加权",
        ),
    )
