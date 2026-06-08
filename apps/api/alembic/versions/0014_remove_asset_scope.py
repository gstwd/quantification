"""移除 strategy_config.asset_scope 字段

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("strategy_config", "asset_scope")


def downgrade() -> None:
    op.add_column(
        "strategy_config",
        sa.Column(
            "asset_scope",
            sa.String(64),
            nullable=False,
            server_default="a_share_etf",
            comment="资产范围",
        ),
    )
