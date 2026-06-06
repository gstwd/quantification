"""添加 backtest_index_result 表缺失列：original_score

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtest_index_result",
        sa.Column(
            "original_score",
            sa.Float,
            nullable=True,
            comment="保留原始综合得分（配置模式下不会被权重值覆盖）",
        ),
    )


def downgrade() -> None:
    op.drop_column("backtest_index_result", "original_score")
